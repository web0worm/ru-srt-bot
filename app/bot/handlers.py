import asyncio
import contextvars
import base64
import logging
import time as _time
import random
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from typing import Optional

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

from ..config import Settings
from ..core import storage
from ..core.server_config import get_server_by_id, get_server_id_by_port
from ..core.server_manager import start_stream_on_server, check_server_availability
from ..core import reviews_storage
from ..core import users_storage
from ..core.models import (
    create_incoming_stream,
    create_outgoing_stream,
    StreamStatus,
)
from ..core.ffmpeg_manager import (
    start_incoming_ffmpeg,
    start_outgoing_ffmpeg,
    stop_incoming_stream,
    collect_stream_stats_data,
    format_deletion_message,
)
from ..core.analyzer import (
    parse_ffmpeg_logs,
    parse_duration,
)
from . import messages
from .keyboards import (
    main_menu_keyboard,
    admin_menu_keyboard,
    incoming_list_inline_keyboard,
    yes_no_keyboard,
    reviews_keyboard,
    rating_keyboard,
    server_selection_keyboard,
    BTN_ADMIN,
)

router = Router()
logger = logging.getLogger(__name__)

STREAM_DURATION = 24 * 60 * 60
_admin_id: int | None = None
_current_user: contextvars.ContextVar[int] = contextvars.ContextVar("_current_user", default=0)


@router.message.middleware()
async def track_user_mw(handler, event, data):
    if hasattr(event, "from_user") and event.from_user:
        _current_user.set(event.from_user.id)
    return await handler(event, data)


@router.callback_query.middleware()
async def track_user_cb_mw(handler, event, data):
    if hasattr(event, "from_user") and event.from_user:
        _current_user.set(event.from_user.id)
    return await handler(event, data)


def _mkb(user_id: int | None = None):
    from aiogram.types import ReplyKeyboardMarkup
    uid = user_id or _current_user.get(0)
    if uid and _admin_id and uid == _admin_id:
        return admin_menu_keyboard()
    return main_menu_keyboard()

MSK_TZ = timezone(timedelta(hours=3))

# Valid 1x1 JPEG placeholder
_PLACEHOLDER_JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wCEAAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB"
    "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQH/2wCEAQEBAQEBA"
    "QEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ"
    "EBAQEBAQEBAQEBAQEBAQH/wAARCAAaABoDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAA"
    "AAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAgP/xAAUEQEAAA"
    "AAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCfAAB//9k="
)


# =================== AVATARS ===================

def _avatars_dir() -> Path:
    d = Path("/opt/srt-bot/avatars")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_placeholder_avatar(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(_PLACEHOLDER_JPEG_B64))
    except Exception:
        pass


def _looks_like_jpeg(path: Path) -> bool:
    try:
        b = path.read_bytes()[:2]
        return b == b"\xFF\xD8"
    except Exception:
        return False


async def ensure_user_avatar(bot, user_id: int) -> None:
    out_path = _avatars_dir() / f"{user_id}.jpg"
    if out_path.exists() and out_path.stat().st_size >= 5_000 and _looks_like_jpeg(out_path):
        return

    tmp_path = out_path.with_suffix(".tmp")
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
        if not photos.total_count or not photos.photos:
            _write_placeholder_avatar(out_path)
            return

        photo = photos.photos[0][-1]
        file = await bot.get_file(photo.file_id)
        await bot.download_file(file.file_path, destination=tmp_path)

        if (not tmp_path.exists()) or tmp_path.stat().st_size < 5_000 or (not _looks_like_jpeg(tmp_path)):
            _write_placeholder_avatar(out_path)
            return

        tmp_path.replace(out_path)
    except Exception:
        _write_placeholder_avatar(out_path)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass


# =================== TIME ===================

def _parse_dt_any(v) -> Optional[datetime]:
    if v is None:
        return None

    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(float(v), tz=timezone.utc)
        except Exception:
            return None

    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    s = str(v).strip()
    if not s:
        return None

    if s.endswith("UTC"):
        s2 = s.replace(" UTC", "").strip()
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(s2, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except Exception:
                pass

    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass

    try:
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _fmt_dt_msk(label: str, raw_dt) -> str:
    dt_utc = _parse_dt_any(raw_dt)
    if not dt_utc:
        return f"{label}: —"
    dt_msk = dt_utc.astimezone(MSK_TZ)
    return f"{label}: {dt_msk.strftime('%Y-%m-%d %H:%M:%S')} MSK"


# =================== ADMIN ===================

async def notify_admin(bot, settings: Settings, text: str) -> None:
    admin_id = getattr(settings, "admin_user_id", None)
    if not admin_id:
        return
    try:
        await bot.send_message(
            admin_id,
            f"🚨 <b>ADMIN</b>\n{text}",
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        pass


# =================== FSM ===================

class IncomingCreation(StatesGroup):
    waiting_for_server_selection = State()
    waiting_for_name = State()
    waiting_for_passphrase_needed = State()
    waiting_for_passphrase = State()


class OutgoingCreation(StatesGroup):
    waiting_for_incoming_selection = State()
    waiting_for_passphrase_needed = State()
    waiting_for_passphrase = State()


class ManageFlow(StatesGroup):
    waiting_for_incoming_selection = State()


class ReviewCreation(StatesGroup):
    waiting_for_rating = State()
    waiting_for_text = State()


class MessageFlow(StatesGroup):
    waiting_for_message = State()
    waiting_for_confirm = State()


# =================== /start ===================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, settings: Settings):
    global _admin_id
    _admin_id = getattr(settings, "admin_user_id", None)
    users_storage.track_user(message.from_user.id, settings)
    await ensure_user_avatar(message.bot, message.from_user.id)
    await state.clear()
    await message.answer(messages.START_MESSAGE, reply_markup=_mkb())
    await message.answer(messages.MAIN_MENU_PROMPT, reply_markup=_mkb())


# =================== /reset (admin only) ===================

@router.message(Command("reset"))
async def cmd_reset(message: Message, state: FSMContext, settings: Settings):
    admin_id = getattr(settings, "admin_user_id", None)
    if not admin_id or message.from_user.id != admin_id:
        return

    await state.clear()
    await message.answer("Reset in progress…", reply_markup=_mkb())

    state_obj = storage.load_state(settings)

    for inc in list(state_obj.incoming_streams):
        try:
            await asyncio.wait_for(
                asyncio.to_thread(stop_incoming_stream, inc, settings, state_obj),
                timeout=25.0,
            )
        except Exception:
            logger.exception("reset stop failed stream_id=%s", getattr(inc, "id", "?"))

    state_obj.incoming_streams = []
    storage.save_state(state_obj, settings)
    await message.answer("Reset done.", reply_markup=_mkb())


# =================== BROADCAST (admin only) ===================

@router.message(Command("message"))
async def cmd_broadcast(message: Message, state: FSMContext, settings: Settings):
    admin_id = getattr(settings, "admin_user_id", None)
    if not admin_id or message.from_user.id != admin_id:
        return

    user_count = len(users_storage.load_user_ids(settings))
    await state.set_state(MessageFlow.waiting_for_message)
    await message.answer(
        f"📢 <b>Рассылка</b>\n\n"
        f"Пользователей в базе: <b>{user_count}</b>\n\n"
        f"Отправьте сообщение, которое получат все пользователи.\n"
        f"Можно отправить текст, фото с подписью или видео.\n\n"
        f"Для отмены — /cancel",
        parse_mode="HTML",
        reply_markup=_mkb(),
    )


@router.message(Command("cancel"), MessageFlow.waiting_for_message)
@router.message(Command("cancel"), MessageFlow.waiting_for_confirm)
async def cmd_broadcast_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Рассылка отменена.", reply_markup=_mkb())


@router.message(MessageFlow.waiting_for_message)
async def broadcast_got_message(message: Message, state: FSMContext, settings: Settings):
    # Сохраняем message_id и chat_id чтобы потом скопировать
    await state.update_data(
        broadcast_chat_id=message.chat.id,
        broadcast_message_id=message.message_id,
    )
    await state.set_state(MessageFlow.waiting_for_confirm)

    user_count = len(users_storage.load_user_ids(settings))
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"✅ Отправить {user_count} пользователям", callback_data="message:confirm"),
            ],
            [
                InlineKeyboardButton(text="❌ Отменить", callback_data="message:cancel"),
            ],
        ]
    )
    await message.answer(
        "👆 Вот ваше сообщение выше. Подтвердите отправку:",
        reply_markup=kb,
    )


@router.callback_query(MessageFlow.waiting_for_confirm, F.data == "message:confirm")
async def broadcast_confirm(callback: CallbackQuery, state: FSMContext, settings: Settings):
    await callback.answer()
    data = await state.get_data()
    chat_id = data.get("broadcast_chat_id")
    message_id = data.get("broadcast_message_id")
    await state.clear()

    if not chat_id or not message_id:
        await callback.message.answer("Ошибка: сообщение не найдено.", reply_markup=_mkb())
        return

    user_ids = users_storage.load_user_ids(settings)
    total = len(user_ids)
    sent = 0
    failed = 0

    progress_msg = await callback.message.answer(f"📤 Отправка… 0/{total}")

    for uid in user_ids:
        try:
            await callback.message.bot.copy_message(
                chat_id=uid,
                from_chat_id=chat_id,
                message_id=message_id,
            )
            sent += 1
        except Exception:
            failed += 1

        # Обновляем прогресс каждые 10 пользователей
        if (sent + failed) % 10 == 0:
            try:
                await progress_msg.edit_text(f"📤 Отправка… {sent + failed}/{total}")
            except Exception:
                pass

        # Telegram rate limit: ~30 msg/sec
        await asyncio.sleep(0.05)

    try:
        await progress_msg.delete()
    except Exception:
        pass

    await callback.message.answer(
        f"✅ <b>Рассылка завершена</b>\n\n"
        f"📨 Отправлено: {sent}\n"
        f"❌ Не доставлено: {failed}\n"
        f"👥 Всего: {total}",
        parse_mode="HTML",
        reply_markup=_mkb(),
    )


@router.callback_query(MessageFlow.waiting_for_confirm, F.data == "message:cancel")
async def broadcast_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.answer("Рассылка отменена.", reply_markup=_mkb())


# =================== SUPPORT (main menu) ===================

@router.message(F.text == messages.BTN_SUPPORT)
async def handle_support(message: Message):
    text = random.choice(messages.SUPPORT_MENU_TEXTS)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Поддержать ff264", url=messages.SUPPORT_URL)]
        ]
    )
    await message.answer(text, reply_markup=kb, disable_web_page_preview=True)



# =================== REVIEWS ===================

@router.message(F.text == messages.BTN_REVIEWS)
async def handle_reviews_menu(message: Message, settings: Settings):
    users_storage.track_user(message.from_user.id, settings)
    await message.answer(messages.REVIEWS_MENU, reply_markup=reviews_keyboard())


@router.callback_query(F.data == "review:create")
async def handle_review_create(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ReviewCreation.waiting_for_rating)
    await callback.message.answer(messages.REVIEW_ASK_RATING, reply_markup=rating_keyboard())


@router.callback_query(F.data.startswith("rating:"))
async def handle_rating(callback: CallbackQuery, state: FSMContext, settings: Settings):
    await callback.answer()
    rating = int(callback.data.split(":")[1])
    await state.update_data(rating=rating)
    await state.set_state(ReviewCreation.waiting_for_text)
    stars = "⭐" * rating
    await callback.message.answer(f"Вы выбрали: {stars}\n{messages.REVIEW_ASK_TEXT}")


@router.message(Command("skip"), ReviewCreation.waiting_for_text)
async def handle_review_skip_text(message: Message, state: FSMContext, settings: Settings):
    data = await state.get_data()
    rating = data.get("rating")
    if rating:
        username = message.from_user.username or f"User_{message.from_user.id}"
        username_display = f"@{username}" if message.from_user.username else f"User {message.from_user.id}"
        review = reviews_storage.Review(user_id=message.from_user.id, username=username_display, rating=rating, text=None)
        reviews_storage.save_review(review, settings)
        await message.answer(messages.REVIEW_NO_TEXT, reply_markup=_mkb())
    else:
        await message.answer("Ошибка: рейтинг не найден.", reply_markup=_mkb())
    await state.clear()


@router.message(ReviewCreation.waiting_for_text, F.text)
async def handle_review_text(message: Message, state: FSMContext, settings: Settings):
    data = await state.get_data()
    rating = data.get("rating")
    text = message.text
    if rating:
        username = message.from_user.username or f"User_{message.from_user.id}"
        username_display = f"@{username}" if message.from_user.username else f"User {message.from_user.id}"
        review = reviews_storage.Review(user_id=message.from_user.id, username=username_display, rating=rating, text=text)
        reviews_storage.save_review(review, settings)
        await message.answer(messages.REVIEW_CREATED, reply_markup=_mkb())
    else:
        await message.answer("Ошибка: рейтинг не найден.", reply_markup=_mkb())
    await state.clear()


@router.callback_query(F.data == "review:list")
async def handle_review_list(callback: CallbackQuery, settings: Settings):
    """Показать все отзывы (все видят все отзывы, админ может удалять)"""
    await callback.answer()
    
    reviews = reviews_storage.get_reviews_list(settings, limit=20)
    
    if not reviews:
        await callback.message.answer(messages.REVIEW_NO_REVIEWS, reply_markup=_mkb())
        return
    
    user_id = callback.from_user.id
    admin_id = getattr(settings, "admin_user_id", None)
    is_admin = admin_id is not None and user_id == admin_id
    
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    
    lines = [messages.REVIEW_LIST_HEADER]
    buttons = []
    
    # Show all reviews to everyone
    for idx, r in enumerate(reviews):
        s = "⭐" * r.rating
        review_num = idx + 1
        if r.text:
            lines.append(f"{messages.REVIEW_FORMAT.format(rating=s, username=r.username, text=r.text)}")
        else:
            lines.append(f"{messages.REVIEW_FORMAT_NO_TEXT.format(rating=s, username=r.username)}")
    
    # Only admin can delete - show delete buttons with review preview
    if is_admin:
        for j, r in enumerate(reviews):
            stars = "⭐" * r.rating
            # Truncate review text for button
            preview = ""
            if r.text:
                preview = r.text[:25] + ("..." if len(r.text) > 25 else "")
            btn_text = f"🗑 {j+1}. {stars} {r.username}"
            if preview:
                btn_text = f"🗑 {j+1}. {stars} {preview}"
            # Telegram button text limit is 64 chars
            if len(btn_text) > 60:
                btn_text = btn_text[:57] + "..."
            buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"delete_review:{j}")])
    
    # Always add back button
    buttons.append([InlineKeyboardButton(text="Назад", callback_data="review:back")])
    
    t = "".join(lines)
    if len(t) > 3500:  # Leave space for buttons
        t = t[:3500] + "\n\n... (показаны последние отзывы)"
    
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.answer(t, reply_markup=kb, disable_web_page_preview=True)

@router.callback_query(F.data.startswith("delete_review:"))
async def handle_delete_review(callback: CallbackQuery, settings: Settings):
    """Удаление отзыва (только для администратора)"""
    await callback.answer()
    
    user_id = callback.from_user.id
    admin_id = getattr(settings, "admin_user_id", None)
    is_admin = admin_id is not None and user_id == admin_id
    
    if not is_admin:
        await callback.message.answer("Только администратор может удалять отзывы.", reply_markup=_mkb())
        return
    
    try:
        review_idx = int(callback.data.split(":")[1])
        
        state = reviews_storage.load_reviews(settings)
        revs = reviews_storage.get_reviews_list(settings, limit=20)
        
        if 0 <= review_idx < len(revs):
            # Find actual index in state (reversed list)
            actual_idx = len(state.reviews) - 1 - review_idx
            if 0 <= actual_idx < len(state.reviews):
                state.reviews.pop(actual_idx)
                reviews_storage.save_reviews(state, settings)
                await callback.message.answer("Отзыв удален.", reply_markup=_mkb())
            else:
                await callback.message.answer("Ошибка при удалении.", reply_markup=_mkb())
        else:
            await callback.message.answer("Отзыв не найден.", reply_markup=_mkb())
    except Exception as e:
        await callback.message.answer(f"Ошибка: {str(e)}", reply_markup=_mkb())

@router.callback_query(F.data == "review:back")
async def handle_review_back(callback: CallbackQuery):
    """Вернуться к меню отзывов"""
    await callback.answer()
    await callback.message.answer(messages.REVIEWS_MENU, reply_markup=reviews_keyboard())




# =================== ADMIN PANEL ===================

@router.message(F.text == BTN_ADMIN)
async def handle_admin_button(message: Message, settings: Settings):
    admin_id = getattr(settings, "admin_user_id", None)
    if not admin_id or message.from_user.id != admin_id:
        return

    from ..core.server_config import get_servers_config
    servers = get_servers_config()

    buttons = [
        [InlineKeyboardButton(text="\U0001f4ca Статистика", callback_data="adm:stats")],
        [InlineKeyboardButton(text="\U0001f465 Пользователи", callback_data="adm:users")],
    ]
    for srv in servers:
        flag = {"spb": "\U0001f1f7\U0001f1fa", "msk": "\U0001f3d9"}.get(srv.id, "\U0001f310")
        buttons.append([
            InlineKeyboardButton(
                text=f"\U0001f504 Ресет {flag} {srv.name}",
                callback_data=f"adm:reset:{srv.id}",
            )
        ])
    buttons.append([InlineKeyboardButton(text="\U0001f4e2 Рассылка", callback_data="adm:broadcast")])
    buttons.append([InlineKeyboardButton(text="\u274c Закрыть", callback_data="adm:close")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("\U0001f527 <b>Админ-панель</b>", parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "adm:close")
async def admin_close(callback: CallbackQuery, settings: Settings):
    admin_id = getattr(settings, "admin_user_id", None)
    if not admin_id or callback.from_user.id != admin_id:
        return
    await callback.answer()
    await callback.message.delete()


@router.callback_query(F.data == "adm:stats")
async def admin_stats(callback: CallbackQuery, settings: Settings):
    admin_id = getattr(settings, "admin_user_id", None)
    if not admin_id or callback.from_user.id != admin_id:
        return
    await callback.answer()

    from ..core.server_config import get_servers_config, get_server_by_id, get_server_id_by_port
    state_obj = storage.load_state(settings)
    all_users = users_storage.load_user_ids(settings)
    active = [s for s in state_obj.incoming_streams if s.status == StreamStatus.RUNNING]
    servers = get_servers_config()

    lines = ["\U0001f4ca <b>Статистика</b>", "",
        f"\U0001f465 Юзеров: <b>{len(all_users)}</b>",
        f"\U0001f7e2 Активных потоков: <b>{len(active)}</b>"]

    for srv in servers:
        flag = {"spb": "\U0001f1f7\U0001f1fa", "msk": "\U0001f3d9"}.get(srv.id, "\U0001f310")
        srv_streams = [s for s in active if getattr(s, 'server_id', 'spb') == srv.id]
        total_out = sum(len(s.outgoing_streams or []) for s in srv_streams)
        lines.append(f"  {flag} {srv.name}: {len(srv_streams)} in / {total_out} out")

    if active:
        lines += ["", "\U0001f4fa <b>Потоки:</b>"]
        now = _time.time()
        for inc in active:
            try:
                chat = await callback.message.bot.get_chat(inc.user_id)
                who = f"@{chat.username}" if chat.username else f"id:{inc.user_id}"
            except Exception:
                who = f"id:{inc.user_id}"
            uptime_sec = int(now - (inc.start_time or now))
            h, rem = divmod(uptime_sec, 3600)
            m = rem // 60
            uptime_str = f"{h}ч {m}м" if h else f"{m}м"
            exp = getattr(inc, 'expires_at', None)
            exp_str = ""
            if exp:
                left = max(0, int(exp - now))
                lh, lr = divmod(left, 3600)
                lm = lr // 60
                exp_str = f" ({lh}ч{lm}м)" if lh else f" ({lm}м)"
            outs = len(inc.outgoing_streams or [])
            out_str = f" +{outs}out" if outs else ""
            lines.append(f"  • {who} <b>{inc.name}</b> :{inc.local_port_in} {uptime_str}{exp_str}{out_str}")

    await callback.message.answer("\n".join(lines), parse_mode="HTML")


@router.callback_query(F.data == "adm:users")
async def admin_users(callback: CallbackQuery, settings: Settings):
    admin_id = getattr(settings, "admin_user_id", None)
    if not admin_id or callback.from_user.id != admin_id:
        return
    await callback.answer()
    user_ids = sorted(users_storage.load_user_ids(settings))
    lines = [f"\U0001f465 <b>Пользователи ({len(user_ids)}):</b>", ""]
    for uid in user_ids:
        try:
            chat = await callback.message.bot.get_chat(uid)
            name = chat.full_name or ""
            uname = f"@{chat.username}" if chat.username else "нет username"
            lines.append(f"  • <b>{uid}</b> — {name} ({uname})")
        except Exception:
            lines.append(f"  • <b>{uid}</b> — (недоступен)")
    await callback.message.answer("\n".join(lines), parse_mode="HTML")


@router.callback_query(F.data.startswith("adm:reset:"))
async def admin_reset_server(callback: CallbackQuery, settings: Settings):
    admin_id = getattr(settings, "admin_user_id", None)
    if not admin_id or callback.from_user.id != admin_id:
        return
    server_id = callback.data.split(":")[2]
    from ..core.server_config import get_server_by_id
    srv = get_server_by_id(server_id)
    srv_name = srv.name if srv else server_id

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"\u2705 Да, сбросить {srv_name}", callback_data=f"adm:reset_confirm:{server_id}")],
        [InlineKeyboardButton(text="\u274c Отмена", callback_data="adm:close")],
    ])
    await callback.answer()
    state_obj = storage.load_state(settings)
    count = sum(1 for s in state_obj.incoming_streams if getattr(s, 'server_id', 'spb') == server_id)
    await callback.message.answer(
        f"\u26a0\ufe0f Сбросить <b>{srv_name}</b>?\n"
        f"Будет остановлено <b>{count}</b> потоков.\n"
        f"Другие серверы НЕ затронуты.",
        parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("adm:reset_confirm:"))
async def admin_reset_confirm(callback: CallbackQuery, settings: Settings):
    admin_id = getattr(settings, "admin_user_id", None)
    if not admin_id or callback.from_user.id != admin_id:
        return
    await callback.answer()
    server_id = callback.data.split(":")[2]
    from ..core.server_config import get_server_by_id
    srv = get_server_by_id(server_id)
    srv_name = srv.name if srv else server_id
    await callback.message.answer(f"\U0001f504 Сбрасываю {srv_name}...")
    state_obj = storage.load_state(settings)
    to_kill = [s for s in state_obj.incoming_streams if getattr(s, 'server_id', 'spb') == server_id]
    killed = 0
    for inc in to_kill:
        try:
            await asyncio.wait_for(asyncio.to_thread(stop_incoming_stream, inc, settings, state_obj), timeout=25.0)
            killed += 1
        except Exception:
            pass
    state_obj.incoming_streams = [s for s in state_obj.incoming_streams if getattr(s, 'server_id', 'spb') != server_id]
    storage.save_state(state_obj, settings)
    await callback.message.answer(f"\u2705 {srv_name} сброшен. Остановлено: {killed}.", reply_markup=_mkb())


@router.callback_query(F.data == "adm:broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext, settings: Settings):
    admin_id = getattr(settings, "admin_user_id", None)
    if not admin_id or callback.from_user.id != admin_id:
        return
    await callback.answer()
    user_count = len(users_storage.load_user_ids(settings))
    await state.set_state(MessageFlow.waiting_for_message)
    await callback.message.answer(
        f"\U0001f4e2 <b>Рассылка</b>\n\nПользователей: <b>{user_count}</b>\n\n"
        f"Отправьте сообщение для всех.\nОтмена — /cancel",
        parse_mode="HTML", reply_markup=_mkb())


# =================== STREAM RENEWAL ===================

@router.callback_query(F.data.startswith("renew:"))
async def handle_renew(callback: CallbackQuery, settings: Settings):
    await callback.answer()
    stream_id = callback.data.split(":", 1)[1]
    state_obj = storage.load_state(settings)
    incoming = storage.get_incoming_stream_by_id(state_obj, stream_id)
    if not incoming or incoming.status != StreamStatus.RUNNING:
        await callback.message.answer("Поток не найден.", reply_markup=_mkb())
        return
    if incoming.user_id != callback.from_user.id:
        admin_id = getattr(settings, "admin_user_id", None)
        if not admin_id or callback.from_user.id != admin_id:
            return
    incoming.expires_at = _time.time() + STREAM_DURATION
    storage.save_state(state_obj, settings)
    await callback.message.answer(
        f"\u2705 Поток <b>{incoming.name}</b> продлён на 24 часа.",
        parse_mode="HTML", reply_markup=_mkb())
    await notify_admin(callback.message.bot, settings,
        f"Поток продлён\nUser: {callback.from_user.id}\nПоток: {incoming.name}")


@router.callback_query(F.data.startswith("renew_skip:"))
async def handle_renew_skip(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer("Хорошо, поток остановится по истечении времени.", reply_markup=_mkb())



@router.callback_query(IncomingCreation.waiting_for_server_selection, F.data.startswith("server:"))
async def handle_server_selection(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора сервера"""
    await callback.answer()
    server_id = callback.data.split(":")[1]  # "spb" или "msk"
    await state.update_data(server_id=server_id)
    await state.set_state(IncomingCreation.waiting_for_name)
    await callback.message.answer(messages.ASK_INCOMING_NAME, reply_markup=_mkb())

@router.message(F.text == messages.BTN_CREATE_INCOMING)
async def handle_create_incoming(message: Message, state: FSMContext, settings: Settings):
    users_storage.track_user(message.from_user.id, settings)
    await state.set_state(IncomingCreation.waiting_for_server_selection)
    await message.answer(messages.ASK_SERVER_SELECTION, reply_markup=server_selection_keyboard())


@router.message(F.text == messages.BTN_LIST_MY_STREAMS)
async def handle_stream_menu(message: Message, state: FSMContext, settings: Settings):
    state_obj = storage.load_state(settings)
    user_id = message.from_user.id

    is_admin = bool(getattr(settings, "admin_user_id", None)) and user_id == settings.admin_user_id
    streams_all = state_obj.incoming_streams if is_admin else storage.get_user_incoming_streams(state_obj, user_id)
    running_streams = [s for s in streams_all if s.status == StreamStatus.RUNNING]

    if not running_streams:
        await message.answer("Нет активных потоков.", reply_markup=_mkb())
        return

    kb = incoming_list_inline_keyboard(
        running_streams,
        current_user_id=user_id,
        admin_user_id=getattr(settings, "admin_user_id", None),
    )
    await state.set_state(ManageFlow.waiting_for_incoming_selection)
    await message.answer("Выберите поток для управления:", reply_markup=kb)


@router.message(F.text == messages.BTN_ADD_OUTGOING)
async def handle_add_outgoing(message: Message, state: FSMContext, settings: Settings):
    state_obj = storage.load_state(settings)
    user_id = message.from_user.id

    is_admin = bool(getattr(settings, "admin_user_id", None)) and user_id == settings.admin_user_id
    streams_all = state_obj.incoming_streams if is_admin else storage.get_user_incoming_streams(state_obj, user_id)
    running_streams = [s for s in streams_all if s.status == StreamStatus.RUNNING]

    if not running_streams:
        await message.answer("Нет активных входящих потоков.", reply_markup=_mkb())
        return

    await state.set_state(OutgoingCreation.waiting_for_incoming_selection)
    kb = incoming_list_inline_keyboard(
        running_streams,
        current_user_id=user_id,
        admin_user_id=getattr(settings, "admin_user_id", None),
    )
    await message.answer(messages.ASK_SELECT_INCOMING_FOR_OUTGOING, reply_markup=kb)


# =================== TUNNEL REMINDERS ===================

@router.callback_query(F.data.startswith("tunnel_reminder:"))
async def handle_tunnel_reminder(callback: CallbackQuery, settings: Settings):
    """Обработка напоминания о долго работающем туннеле"""
    await callback.answer()
    
    try:
        parts = callback.data.split(":")
        action = parts[1]  # "keep" or "stop"
        stream_id = parts[2]
        
        user_id = callback.from_user.id
        state_obj = storage.load_state(settings)
        
        incoming = None
        for s in state_obj.incoming_streams:
            if s.id == stream_id and s.user_id == user_id:
                incoming = s
                break
        
        if not incoming:
            await callback.message.answer("Туннель не найден.", reply_markup=_mkb())
            return
        
        if action == "stop":
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(stop_incoming_stream, incoming, settings, state_obj),
                    timeout=25.0,
                )
                await callback.message.answer("Туннель остановлен и удален.", reply_markup=_mkb())
                
                admin_text = f"Пользователь {user_id} остановил туннель по напоминанию\nПорт: {incoming.local_port_in}\nНазвание: {incoming.name}"
                await notify_admin(callback.message.bot, settings, admin_text)
            except Exception as e:
                logger.exception("Failed to stop tunnel from reminder")
                await callback.message.answer(f"Ошибка при остановке туннеля.", reply_markup=_mkb())
        else:  # keep
            reminder_file = Path(settings.state_file).parent / f"reminder_{user_id}.json"
            reminder_data = {
                "last_reminder": datetime.now(timezone.utc).isoformat(),
                "acknowledged": True
            }
            reminder_file.write_text(json.dumps(reminder_data), encoding='utf-8')
            
            await callback.message.answer("Хорошо, туннель продолжит работать. Напоминание придет через 12 часов, если туннель все еще будет активен.", reply_markup=_mkb())
    
    except Exception as e:
        logger.exception("tunnel_reminder handler error")
        await callback.message.answer("Произошла ошибка.", reply_markup=_mkb())

# =================== CREATE INCOMING ===================

@router.message(IncomingCreation.waiting_for_name)
async def incoming_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not name:
        await message.answer(messages.INVALID_INPUT, reply_markup=_mkb())
        return

    await state.update_data(name=name)
    await state.set_state(IncomingCreation.waiting_for_passphrase_needed)
    await message.answer(messages.ASK_PASSPHRASE_NEEDED, reply_markup=yes_no_keyboard())


@router.message(IncomingCreation.waiting_for_passphrase_needed)
async def incoming_passphrase_needed(message: Message, state: FSMContext, settings: Settings):
    answer = (message.text or "").strip().lower()

    if answer in ("да", "yes", "y"):
        await state.set_state(IncomingCreation.waiting_for_passphrase)
        await message.answer(messages.ASK_PASSPHRASE_IN, reply_markup=_mkb())
        return

    if answer in ("нет", "no", "n"):
        data = await state.get_data()
        await finalize_incoming_creation(message, state, settings, data, passphrase=None)
        return

    await message.answer(messages.INVALID_INPUT, reply_markup=yes_no_keyboard())


@router.message(IncomingCreation.waiting_for_passphrase)
async def incoming_passphrase(message: Message, state: FSMContext, settings: Settings):
    passphrase = (message.text or "").strip()
    if len(passphrase) < 10:
        await message.answer(messages.PASSPHRASE_TOO_SHORT, reply_markup=_mkb())
        return

    data = await state.get_data()
    await finalize_incoming_creation(message, state, settings, data, passphrase=passphrase)


async def finalize_incoming_creation(
    message: Message,
    state: FSMContext,
    settings: Settings,
    data: dict,
    passphrase: Optional[str],
) -> None:
    try:
        await ensure_user_avatar(message.bot, message.from_user.id)

        state_obj = storage.load_state(settings)

        if storage.count_running_incoming_streams(state_obj) >= settings.max_incoming_streams:
            await state.clear()
            await message.answer(messages.INCOMING_LIMIT_REACHED, reply_markup=_mkb())
            return

        # Получаем server_id из данных (по умолчанию "spb" для обратной совместимости)
        server_id = data.get("server_id", "spb")
        logger.info(f"finalize_incoming: data={data}, server_id={server_id}")
        server_config = get_server_by_id(server_id)
        
        if not server_config:
            await state.clear()
            await message.answer(f"Ошибка: сервер {server_id} не найден в конфигурации.", reply_markup=_mkb())
            return
        
        # Проверяем доступность сервера
        if not check_server_availability(server_config):
            await state.clear()
            await message.answer(f"Сервер {server_config.name} недоступен. Попробуйте другой сервер.", reply_markup=_mkb())
            return
        
        # Выделяем порт с учетом диапазона портов выбранного сервера
        # Используем диапазон портов выбранного сервера
        start_port = server_config.incoming_port_start
        end_port = server_config.incoming_port_end
        
        # Получаем ВСЕ использованные порты в диапазоне сервера
        used_ports = {
            s.local_port_in
            for s in state_obj.incoming_streams
            if start_port <= s.local_port_in <= end_port
        }
        
        port = None
        for p in range(start_port, end_port + 1):
            if p not in used_ports:
                port = p
                break
        
        if port is None:
            await state.clear()
            await message.answer(f"Нет свободных входящих портов на сервере {server_config.name} (диапазон {start_port}-{end_port}).", reply_markup=_mkb())
            return

        stream = create_incoming_stream(
            user_id=message.from_user.id,
            name=data["name"],
            local_port_in=port,
            remote_host_in="0.0.0.0",
            remote_port_in=port,
            passphrase_in=passphrase,
            latency=120,
            server_id=server_id,
            expires_at=_time.time() + STREAM_DURATION,
        )

        logger.info(f"Created stream with server_id={stream.server_id}, port={stream.local_port_in}")
        state_obj.incoming_streams.append(stream)

        try:
            # Запускаем поток через server_manager
            stream_config = {
                "id": stream.id,
                "local_port_in": stream.local_port_in,
                "internal_port": stream.internal_port,  # Добавляем internal_port
                "passphrase_in": stream.passphrase_in,
                "latency_in": stream.latency_in,
            }
            success, pid, log_path = start_stream_on_server(
                server_config,
                "incoming",
                stream_config,
                settings.logs_dir
            )
            
            if not success:
                raise Exception(f"Не удалось запустить поток на сервере {server_config.name}")
            
            stream.pid = pid
            stream.log_path = log_path
            stream.status = StreamStatus.RUNNING
            import time
            stream.start_time = time.time()
        except Exception as e_ff:
            await state.clear()
            await message.answer(
                f"{messages.INCOMING_CREATE_ERROR}\n\nДетали: {e_ff}",
                reply_markup=_mkb(),
            )
            return

        storage.save_state(state_obj, settings)
        await state.clear()

        # Используем домен выбранного сервера
        base_url = f"srt://{server_config.domain}:{stream.local_port_in}"
        
        if passphrase:
            details = (
                f"Название: {stream.name}\n"
                f"{base_url}\n"
                f"passphrase= {passphrase}\n"
                f"Статус: {stream.status.value}"
            )
        else:
            details = (
                f"Название: {stream.name}\n"
                f"{base_url}\n"
                f"Статус: {stream.status.value}"
            )
        await message.answer(messages.INCOMING_CREATED_OK.format(details=details), reply_markup=_mkb())

        user = message.from_user
        admin_text = (
            "Новый ВХОДЯЩИЙ поток\n"
            f"Пользователь: {user.id} (@{user.username or 'нет username'})\n"
            f"Название: {stream.name}\n"
            f"Порт: {stream.local_port_in}\n"
            f"URL: {base_url}"
        )
        await notify_admin(message.bot, settings, admin_text)

    except Exception:
        logger.exception("finalize_incoming_creation failed")
        await state.clear()
        await message.answer(
            "Внутренняя ошибка при создании входящего потока. Проверьте логи.",
            reply_markup=_mkb(),
        )


# =================== CREATE OUTGOING ===================

@router.callback_query(OutgoingCreation.waiting_for_incoming_selection, F.data.startswith("incoming:"))
async def outgoing_select_incoming(callback: CallbackQuery, state: FSMContext):
    incoming_id = callback.data.split(":", 1)[1]
    await state.update_data(incoming_id=incoming_id)
    await state.set_state(OutgoingCreation.waiting_for_passphrase_needed)
    await callback.message.answer(messages.ASK_PASSPHRASE_OUT_NEEDED, reply_markup=yes_no_keyboard())
    await callback.answer()


@router.message(OutgoingCreation.waiting_for_passphrase_needed)
async def outgoing_passphrase_needed(message: Message, state: FSMContext, settings: Settings):
    answer = (message.text or "").strip().lower()

    if answer in ("да", "yes", "y"):
        await state.set_state(OutgoingCreation.waiting_for_passphrase)
        await message.answer(messages.ASK_PASSPHRASE_OUT, reply_markup=_mkb())
        return

    if answer in ("нет", "no", "n"):
        data = await state.get_data()
        await finalize_outgoing_creation(message, state, settings, data, passphrase=None)
        return

    await message.answer(messages.INVALID_INPUT, reply_markup=yes_no_keyboard())


@router.message(OutgoingCreation.waiting_for_passphrase)
async def outgoing_passphrase(message: Message, state: FSMContext, settings: Settings):
    passphrase = (message.text or "").strip()
    if len(passphrase) < 10:
        await message.answer(messages.PASSPHRASE_TOO_SHORT, reply_markup=_mkb())
        return

    data = await state.get_data()
    await finalize_outgoing_creation(message, state, settings, data, passphrase=passphrase)


async def finalize_outgoing_creation(
    message: Message,
    state: FSMContext,
    settings: Settings,
    data: dict,
    passphrase: Optional[str],
) -> None:
    try:
        await ensure_user_avatar(message.bot, message.from_user.id)

        state_obj = storage.load_state(settings)
        incoming = storage.get_incoming_stream_by_id(state_obj, data["incoming_id"])
        if not incoming:
            await state.clear()
            await message.answer(messages.NO_INCOMING_SELECTED, reply_markup=_mkb())
            return

        # Определяем server_id по порту входящего потока
        incoming_server_id = get_server_id_by_port(incoming.local_port_in)

        # Выделяем порт с учетом server_id
        port_out = storage.allocate_outgoing_port(state_obj, settings, incoming_server_id)
        if port_out is None:
            await state.clear()
            await message.answer(f"Нет свободных исходящих портов на сервере ({incoming_server_id}).", reply_markup=_mkb())
            return
        outgoing = create_outgoing_stream(
            user_id=message.from_user.id,
            local_port_out=port_out,
            remote_host_out="0.0.0.0",
            remote_port_out=port_out,
            passphrase_out=passphrase,
            latency=120,
            server_id=incoming_server_id,  # Используем server_id из входящего потока
        )

        # Убеждаемся что incoming имеет правильный server_id
        incoming.server_id = incoming_server_id
        incoming.outgoing_streams.append(outgoing)

        try:
            # Запускаем исходящий поток через server_manager
            # Убеждаемся что используем правильный server_id из входящего потока
            # Если server_id отсутствует, пытаемся получить из данных потока или используем из outgoing
            server_id_to_use = incoming_server_id
            logger.info(f"Creating outgoing stream: incoming_server_id={incoming_server_id}, port={incoming.local_port_in}, server_id_to_use={server_id_to_use}")  # Используем уже определенный server_id
            server_config = get_server_by_id(server_id_to_use)
            if not server_config:
                raise Exception(f"Сервер {server_id_to_use} не найден в конфигурации")
            
            # Убеждаемся что исходящий поток имеет правильный server_id
            if not hasattr(outgoing, 'server_id') or not outgoing.server_id:
                outgoing.server_id = server_id_to_use
            
            outgoing_config = {
                "id": outgoing.id,
                "local_port_out": outgoing.local_port_out,
                "remote_host_out": server_config.domain if server_config else outgoing.remote_host_out,  # Используем домен сервера
                "remote_port_out": outgoing.remote_port_out,
                "passphrase_out": outgoing.passphrase_out,
                "latency_out": outgoing.latency_out,
                "internal_port": incoming.internal_port,  # Используем internal_port из входящего потока
            }
            
            success, pid, log_path = start_stream_on_server(
                server_config,
                "outgoing",
                outgoing_config,
                settings.logs_dir
            )
            
            if not success:
                raise Exception(f"Не удалось запустить исходящий поток на сервере {server_config.name}")
            
            outgoing.pid = pid
            outgoing.log_path = log_path
            outgoing.status = StreamStatus.RUNNING
            import time
            outgoing.start_time = time.time()
        except Exception as e_ff:
            await state.clear()
            await message.answer(
                f"{messages.OUTGOING_CREATE_ERROR}\n\nДетали: {e_ff}",
                reply_markup=_mkb(),
            )
            return

        storage.save_state(state_obj, settings)
        await state.clear()

        base_url = f"srt://{server_config.domain}:{outgoing.local_port_out}"
        
        if passphrase:
            details = (
                f"Название входящего потока: {incoming.name}\n"
                f"{base_url}\n"
                f"passphrase= {passphrase}\n"
                f"Статус: {outgoing.status.value}"
            )
        else:
            details = (
                f"Название входящего потока: {incoming.name}\n"
                f"{base_url}\n"
                f"Статус: {outgoing.status.value}"
            )
        await message.answer(messages.OUTGOING_CREATED_OK.format(details=details), reply_markup=_mkb())

        user = message.from_user
        admin_text = (
            "Новый ИСХОДЯЩИЙ поток\n"
            f"Пользователь: {user.id} (@{user.username or 'нет username'})\n"
            f"От входящего: {incoming.name} (порт {incoming.local_port_in})\n"
            f"Порт исходящего: {outgoing.local_port_out}\n"
            f"URL: {base_url}"
        )
        await notify_admin(message.bot, settings, admin_text)

    except Exception:
        logger.exception("finalize_outgoing_creation failed")
        await state.clear()
        await message.answer(
            "Внутренняя ошибка при создании исходящего потока. Проверьте логи.",
            reply_markup=_mkb(),
        )


# =================== STREAM MANAGEMENT ===================

@router.callback_query(ManageFlow.waiting_for_incoming_selection, F.data.startswith("incoming:"))
async def manage_incoming_callback(callback: CallbackQuery, state: FSMContext, settings: Settings):
    stream_id = callback.data.split(":", 1)[1]
    state_obj = storage.load_state(settings)
    incoming = storage.get_incoming_stream_by_id(state_obj, stream_id)
    if not incoming:
        await callback.answer("Поток не найден.", show_alert=True)
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="ℹ️ Информация", callback_data=f"action:info:{stream_id}"),
                InlineKeyboardButton(text="🗑 Удалить поток", callback_data=f"action:delete:{stream_id}"),
            ]
        ]
    )

    await callback.message.answer(
        f"Выбран поток: {incoming.name} (ID: {incoming.local_port_in})",
        reply_markup=kb,
    )
    await callback.answer()
    await state.clear()


@router.callback_query(F.data.startswith("action:"))
async def manage_action_callback(callback: CallbackQuery, settings: Settings):
    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer("Некорректное действие.", show_alert=True)
        return

    action, stream_id = parts[1], parts[2]
    user_id = callback.from_user.id

    state_obj = storage.load_state(settings)
    incoming = storage.get_incoming_stream_by_id(state_obj, stream_id)
    if not incoming:
        await callback.answer("Поток не найден.", show_alert=True)
        return

    is_admin = bool(getattr(settings, "admin_user_id", None)) and user_id == settings.admin_user_id
    is_owner = incoming.user_id == user_id

    # INFO
    if action == "info":
        await callback.answer()
        await callback.message.answer("Собираю информацию…", reply_markup=_mkb())

        try:
            log_paths = []
            if getattr(incoming, "log_path", None):
                log_paths.append(incoming.log_path)
            for o in (incoming.outgoing_streams or []):
                if getattr(o, "log_path", None):
                    log_paths.append(o.log_path)

            try:
                if log_paths:
                    await asyncio.wait_for(asyncio.to_thread(parse_ffmpeg_logs, log_paths), timeout=8.0)
            except Exception:
                pass

            duration = parse_duration(incoming.start_time, incoming.stop_time)

            # Определяем сервер по порту
            _port = incoming.local_port_in
            _srv = get_server_by_id(get_server_id_by_port(_port))
            _domain = _srv.domain if _srv else settings.server_public_ip
            base_in = f"srt://{_domain}:{incoming.local_port_in}"
            
                        # Get username
            try:
                chat = await callback.message.bot.get_chat(incoming.user_id)
                owner_display = f"@{chat.username}" if chat.username else f"User {incoming.user_id}"
            except Exception:
                owner_display = f"User {incoming.user_id}"
            
            lines = [
                f"Название: {incoming.name}",
                f"Владелец: {owner_display}",
                f"in: {base_in}\npassphrase= {incoming.passphrase_in}" if incoming.passphrase_in else f"in: {base_in}",
                f"Статус: {incoming.status.value}",
                _fmt_dt_msk("Старт", incoming.start_time),
                _fmt_dt_msk("Стоп", incoming.stop_time),
                f"Время работы: {duration}",
                "",
                "Исходящие потоки:",
            ]

            outs = incoming.outgoing_streams or []
            if not outs:
                lines.append("– нет исходящих потоков")
            else:
                for idx, o in enumerate(outs, start=1):
                    base_out = f"srt://{_domain}:{o.local_port_out}"
                    lines.append(f"- Исходящий {idx}: {base_out}\n  passphrase= {o.passphrase_out}, статус: {o.status.value}" if o.passphrase_out else f"- Исходящий {idx}: {base_out}, статус: {o.status.value}")

            await callback.message.answer(
                messages.INFO_HEADER.format(details="\n".join(lines)),
                reply_markup=_mkb(),
                disable_web_page_preview=True,
            )
        except Exception:
            logger.exception("INFO crashed stream_id=%s", stream_id)
            await callback.message.answer("Ошибка получения информации. Проверь логи.", reply_markup=_mkb())

        return

    # DELETE
    if action == "delete":
        if not (is_admin or is_owner):
            await callback.answer("У вас нет прав управлять этим потоком.", show_alert=True)
            return

        await callback.answer()
        await callback.message.answer("Удаляю поток…", reply_markup=_mkb())

        pre_name = incoming.name
        pre_port_in = incoming.local_port_in

        # Get owner username before deletion
        try:
            owner_chat = await callback.message.bot.get_chat(incoming.user_id)
            pre_owner = f"@{owner_chat.username}" if owner_chat.username else f"User {incoming.user_id}"
        except Exception:
            pre_owner = f"User {incoming.user_id}"
        pre_outs = list(incoming.outgoing_streams or [])
        pre_out_count = len(pre_outs)

        try:
            # Останавливаем поток
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(stop_incoming_stream, incoming, settings, state_obj),
                    timeout=25.0,
                )
            except Exception:
                pass

            # Собираем статистику ДО удаления из state
            try:
                stats_data = await asyncio.wait_for(
                    asyncio.to_thread(collect_stream_stats_data, incoming, settings),
                    timeout=15.0,
                )
            except Exception:
                stats_data = {'server_name': '', 'incoming': {}, 'outgoing': []}

            duration = parse_duration(incoming.start_time, incoming.stop_time)

            # Форматируем даты для сообщения
            _start_dt = _parse_dt_any(incoming.start_time)
            _stop_dt = _parse_dt_any(incoming.stop_time)
            start_str = _start_dt.astimezone(MSK_TZ).strftime('%d.%m.%Y %H:%M') if _start_dt else '—'
            stop_str = _stop_dt.astimezone(MSK_TZ).strftime('%H:%M') if _stop_dt else '—'
            # Если старт и стоп в разные дни — показываем полную дату для стопа
            if _start_dt and _stop_dt and _start_dt.date() != _stop_dt.date():
                stop_str = _stop_dt.astimezone(MSK_TZ).strftime('%d.%m.%Y %H:%M')
            stop_str += ' MSK'

            # Удаляем из state
            state_obj.incoming_streams = [s for s in state_obj.incoming_streams if s.id != incoming.id]
            storage.save_state(state_obj, settings)

            # Строим единое красивое сообщение
            support_text = random.choice(messages.SUPPORT_AFTER_STREAM_TEXTS)
            server_name = stats_data.get('server_name', '')
            if not server_name:
                _sid = get_server_id_by_port(pre_port_in)
                _srv = get_server_by_id(_sid)
                server_name = _srv.name if _srv else _sid

            unified_text = format_deletion_message(
                name=pre_name,
                owner=pre_owner,
                port_in=pre_port_in,
                server_name=server_name,
                start_str=start_str,
                stop_str=stop_str,
                duration=duration,
                incoming_stats=stats_data.get('incoming', {}),
                outgoing_list=stats_data.get('outgoing', []),
                support_text=support_text,
            )

            kb = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="☕ Поддержать ff264", url=messages.SUPPORT_URL)]]
            )

            await callback.message.answer(unified_text, reply_markup=kb, disable_web_page_preview=True)

            # Уведомляем админа
            admin_text = (
                f"Удалён поток\n"
                f"Инициатор: {user_id}\n"
                f"Название: {pre_name}\n"
                f"Порт: {pre_port_in}\n"
                f"Владелец: {pre_owner}\n"
                f"Длительность: {duration}\n"
                f"Исходящих: {pre_out_count}"
            )
            await notify_admin(callback.message.bot, settings, admin_text)

        except Exception:
            logger.exception("DELETE crashed stream_id=%s", stream_id)
            await callback.message.answer("Ошибка удаления. Проверь логи.", reply_markup=_mkb())

        return

    await callback.answer("Неизвестное действие.", show_alert=True)

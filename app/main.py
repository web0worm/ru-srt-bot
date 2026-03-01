import asyncio
import logging
import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from .config import load_settings
from .bot.handlers import router as bot_router

from .core.storage import load_state, save_state
from .core.ffmpeg_manager import restart_running_streams, stop_incoming_stream
from .core.models import StreamStatus

logger = logging.getLogger(__name__)

STREAM_DURATION = 24 * 60 * 60
WARN_BEFORE_SEC = 5 * 60


async def auto_expire_streams(bot: Bot, settings, state) -> None:
    warned: set = set()

    while True:
        await asyncio.sleep(30)
        try:
            now = time.time()

            for inc in list(state.incoming_streams):
                expires_at = getattr(inc, 'expires_at', None)
                if not expires_at or inc.status != StreamStatus.RUNNING:
                    continue

                remaining = expires_at - now

                if 0 < remaining <= WARN_BEFORE_SEC and inc.id not in warned:
                    warned.add(inc.id)
                    try:
                        kb = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(
                                text="\u2705 Продлить на 24ч",
                                callback_data=f"renew:{inc.id}",
                                style="success",
                            )],
                            [InlineKeyboardButton(
                                text="\u274c Не продлять",
                                callback_data=f"renew_skip:{inc.id}",
                                style="danger",
                            )],
                        ])
                        mins = max(1, int(remaining // 60))
                        await bot.send_message(
                            inc.user_id,
                            f"\u26a0\ufe0f <b>Туннель скоро истечёт</b>\n\n"
                            f"Поток <b>{inc.name}</b> остановится через ~{mins} мин.\n\n"
                            f"Продлить ещё на 24 часа?",
                            parse_mode="HTML",
                            reply_markup=kb,
                        )
                    except Exception:
                        pass

                if remaining <= 0:
                    try:
                        logger.info("Auto-expiring stream %s (user %s)", inc.id, inc.user_id)
                        await asyncio.wait_for(
                            asyncio.to_thread(stop_incoming_stream, inc, settings, state),
                            timeout=15.0,
                        )
                        state.incoming_streams = [
                            s for s in state.incoming_streams if s.id != inc.id
                        ]
                        warned.discard(inc.id)
                        save_state(state, settings)
                        try:
                            await bot.send_message(
                                inc.user_id,
                                f"\u23f1 <b>Туннель завершён</b>\n\n"
                                f"Поток <b>{inc.name}</b> автоматически остановлен "
                                f"(прошло 24 часа).",
                                parse_mode="HTML",
                            )
                        except Exception:
                            pass
                    except Exception:
                        logger.exception("Failed to auto-expire stream %s", getattr(inc, 'id', '?'))

        except Exception:
            logger.exception("auto_expire_streams loop error")


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    settings = load_settings()
    state = load_state(settings)
    restart_running_streams(settings, state)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher()
    dp["settings"] = settings
    dp["state"] = state
    dp.include_router(bot_router)

    asyncio.create_task(auto_expire_streams(bot, settings, state))

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

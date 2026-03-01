from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from . import messages

BTN_ADMIN = "\U0001f527 Admin"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """
    Главное меню:
    [⬆️ add input] [⬇️ add output]
    [Меню потоков] [Спасибо]
    """
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=messages.BTN_CREATE_INCOMING, style="success"),
                KeyboardButton(text=messages.BTN_ADD_OUTGOING, style="success"),
            ],
            [
                KeyboardButton(text=messages.BTN_LIST_MY_STREAMS),
                KeyboardButton(text=messages.BTN_SUPPORT),
            ],
            [
                KeyboardButton(text=messages.BTN_REVIEWS, style="primary"),
            ],
        ],
        resize_keyboard=True,
    )
    return kb



def admin_menu_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=messages.BTN_CREATE_INCOMING, style="success"),
                KeyboardButton(text=messages.BTN_ADD_OUTGOING, style="success"),
            ],
            [
                KeyboardButton(text=messages.BTN_LIST_MY_STREAMS),
                KeyboardButton(text=messages.BTN_SUPPORT),
            ],
            [
                KeyboardButton(text=messages.BTN_REVIEWS, style="primary"),
            ],
            [
                KeyboardButton(text=BTN_ADMIN, style="danger"),
            ],
        ],
        resize_keyboard=True,
    )
    return kb


def yes_no_keyboard() -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Да", style="success"), KeyboardButton(text="Нет", style="danger")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    return kb


def incoming_list_inline_keyboard(
    streams,
    current_user_id: int | None = None,
    admin_user_id: int | None = None,
) -> InlineKeyboardMarkup:
    rows = []
    is_admin = current_user_id is not None and admin_user_id is not None and (
        current_user_id == admin_user_id
    )

    for s in streams:
        if is_admin:
            owner_mark = " (мой)" if s.user_id == current_user_id else ""
            text = f"{s.name} port: {s.local_port_in}"
        else:
            text = f"{s.name} port: {s.local_port_in}"

        rows.append(
            [InlineKeyboardButton(text=text, callback_data=f"incoming:{s.id}", style="primary")]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)

def reviews_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура для отзывов:
    [Оставить отзыв] [Все отзывы]
    """
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ Оставить отзыв", callback_data="review:create", style="success"),
            ],
            [
                InlineKeyboardButton(text="Все отзывы", callback_data="review:list", style="primary"),
            ],
        ]
    )
    return kb




def server_selection_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура для выбора сервера:
    [Москва] [Петербург]
    """
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🏙 Москва", callback_data="server:msk", style="danger"),
                InlineKeyboardButton(text="🌊 Петербург", callback_data="server:spb", style="primary"),
            ],
        ]
    )
    return kb



def rating_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура для оценки (1-5 звезд)
    """
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐", callback_data="rating:1", style="danger"),
                InlineKeyboardButton(text="⭐⭐", callback_data="rating:2", style="danger"),
                InlineKeyboardButton(text="⭐⭐⭐", callback_data="rating:3"),
                InlineKeyboardButton(text="⭐⭐⭐⭐", callback_data="rating:4", style="success"),
                InlineKeyboardButton(text="⭐⭐⭐⭐⭐", callback_data="rating:5", style="success"),
            ],
        ]
    )
    return kb

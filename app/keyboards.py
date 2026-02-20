from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

def kb_start(is_admin: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="💐 Каталог букетов", callback_data="cat:bouquet"),
        InlineKeyboardButton(text="🌿 Композиции", callback_data="cat:composition"),
    )

    # Можно вернуть эти кнопки, если будет логика под них, пока оставил как заглушки
    builder.row(
        InlineKeyboardButton(text="📦 Мои заявки", callback_data="my:req:list"),
        InlineKeyboardButton(text="💬 Поддержка", callback_data="support"),
    )

   
    
    if is_admin:
        builder.row(InlineKeyboardButton(text="🛠 Админ-панель", callback_data="admin:panel"))
    
    return builder.as_markup()


def kb_price_filters(category: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    # Формат range: min-max. 7000-0 означает "от 7000" (обработаем в коде)
    price_ranges = [
        ("до 2500", "0-2500"),
        ("2500–4000", "2500-4000"),
        ("4000–6000", "4000-6000"),
        ("от 6000", "6000-0"), 
        ("💰 Показать все", "all"),
    ]
    
    for title, price_range in price_ranges:
        builder.button(text=title, callback_data=f"filter:{category}:{price_range}")
    
    builder.button(text="⬅ Назад", callback_data="back:start")
    builder.adjust(2, 2, 1, 1)
    
    return builder.as_markup()


def kb_product_nav(category: str, price_data: str, index: int, total: int, product_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    builder.button(text="✅ Оформить заказ", callback_data=f"req:start:{product_id}")
    
    # Навигация
    # nav:category:price_range:current_index
    prev_idx = max(0, index - 1)
    next_idx = min(total - 1, index + 1)
    
    row_btns = []
    if index > 0:
        row_btns.append(InlineKeyboardButton(text="◀", callback_data=f"nav:{category}:{price_data}:{prev_idx}"))
    
    row_btns.append(InlineKeyboardButton(text=f"{index+1}/{total}", callback_data="noop"))
    
    if index < total - 1:
        row_btns.append(InlineKeyboardButton(text="▶", callback_data=f"nav:{category}:{price_data}:{next_idx}"))

    builder.row(*row_btns)
    
    builder.button(text="🔁 К фильтрам", callback_data=f"cat:{category}")
    builder.button(text="🏠 В меню", callback_data="back:start")
    
    return builder.as_markup()


def kb_delivery_type() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🚚 Доставка", callback_data="req:delivery_type:delivery"),
        InlineKeyboardButton(text="🏃 Самовывоз", callback_data="req:delivery_type:pickup"),
    )
    builder.button(text="❌ Отмена", callback_data="req:cancel")
    return builder.as_markup()


def kb_payment_type(delivery_type: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if delivery_type == "delivery":
        builder.row(
            InlineKeyboardButton(text="💸 Перевод", callback_data="req:pay:transfer"),
            InlineKeyboardButton(text="💳 Карта", callback_data="req:pay:card"),
        )
    else:
        builder.row(
            InlineKeyboardButton(text="💵 Наличные", callback_data="req:pay:cash"),
            InlineKeyboardButton(text="💸 Перевод", callback_data="req:pay:transfer"),
        )
        builder.button(text="💳 Карта", callback_data="req:pay:card")
    builder.button(text="❌ Отменить", callback_data="req:cancel")
    return builder.as_markup()


def kb_confirm() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data="req:confirm:yes"),
        InlineKeyboardButton(text="✏ Изменить", callback_data="req:back:confirm"),
    )
    builder.button(text="❌ Отмена", callback_data="req:cancel")
    return builder.as_markup()


def kb_skip_comment() -> InlineKeyboardMarkup:
    """Клавиатура для пропуска ввода комментария"""
    builder = InlineKeyboardBuilder()
    builder.button(text="Пропустить ➡", callback_data="req:skip_comment")
    return builder.as_markup()


def kb_after_request_sent() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🏠 Главное меню", callback_data="back:start"),
        InlineKeyboardButton(text="📦 Мои заявки", callback_data="my:req:list")
    )
    return builder.as_markup()


def kb_admin_panel() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    builder.row(
        InlineKeyboardButton(text="🆕 Новые", callback_data="admin:req:list:new"),
        InlineKeyboardButton(text="🛠 В работе", callback_data="admin:req:list:in_work")
    )

    
    builder.row(
        InlineKeyboardButton(text="✅ Завершённые", callback_data="admin:req:list:done"),
        InlineKeyboardButton(text="❌ Отменённые", callback_data="admin:req:list:canceled")
    )
    
    
    builder.button(
        text="🎧 Поддержка",
        callback_data="admin:support"
        )
            
    # КНОПКА ДОБАВЛЕНИЯ ТОВАРА УДАЛЕНА
    builder.button(text="⬅ Выход", callback_data="back:start")
    
    return builder.as_markup()

# --- Для списков заявок ---

def kb_my_requests_list(items: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    for request_id, label in items:
        builder.button(
            text=label,
            callback_data=f"my:req:view:{request_id}"
        )

    builder.button(text="⬅ Назад", callback_data="back:start")
    builder.adjust(1)
    return builder.as_markup()


def kb_my_request_view(request_id: int, can_cancel: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if can_cancel:
        builder.button(text="❌ Отменить заявку", callback_data=f"my:req:cancel:{request_id}")
    builder.button(text="⬅ К списку", callback_data="my:req:list")
    builder.adjust(1)
    return builder.as_markup()

def kb_confirm_cancel_my_req(request_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да, отменить", callback_data=f"my:req:cancel_yes:{request_id}"),
        InlineKeyboardButton(text="↩ Нет", callback_data=f"my:req:view:{request_id}")
    )
    return builder.as_markup()


def kb_main_menu_bottom():
    builder = ReplyKeyboardBuilder()
    # Добавляем обычную кнопку, которая будет всегда внизу
    builder.add(KeyboardButton(text="🏠 Главное меню"))
    return builder.as_markup(resize_keyboard=True)
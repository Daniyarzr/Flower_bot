from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from time import time
from typing import Dict, List, Optional, Tuple

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, InputMediaPhoto, Message
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from app.config import Config
from app.db import get_sessionmaker, upsert_user
from app.keyboards import (
    kb_after_request_sent,
    kb_confirm,
    kb_delivery_type,
    kb_main_menu_bottom,
    kb_my_request_view,
    kb_my_requests_list,
    kb_payment_type,
    kb_price_filters,
    kb_product_nav,
    kb_skip_comment,
    kb_start,
)
from app.models import BotText, CategoryEnum, PaymentType, Product, Request, RequestStatus, User, UserRole
from app.states import RequestFSM
from app.utils import is_admin_cached

# Initialize router for user handlers
router = Router(name="user_router")

# Logger setup for this module
logger = logging.getLogger(__name__)

# ==========================
# Catalog Cache
# ==========================
# Cache structure: key = (category, min_price, max_price) -> (products_list, timestamp)
_CATALOG_CACHE: Dict[Tuple[str, int, int], Tuple[List[Product], float]] = {}
CATALOG_TTL = 300  # 5 minutes TTL for cache refresh


async def get_products_cached(category: str, min_p: int, max_p: int) -> List[Product]:
    """ Fetch products from database with caching to reduce query load.
    Cache is invalidated after CATALOG_TTL seconds or if product count changes (e.g., due to additions/deletions).

    :param category: Product category (e.g., 'bouquet')
    :param min_p: Minimum price filter
    :param max_p: Maximum price filter
    :return: List of active products matching filters """
    key = (category, min_p, max_p)
    now = time()

    cached = _CATALOG_CACHE.get(key)
    if cached and now - cached[1] < CATALOG_TTL:
        # Validate cache by checking current DB count
        Session = get_sessionmaker()
        async with Session() as session:
            result = await session.execute(
                select(func.count(Product.id))
                .where(
                    Product.category == CategoryEnum(category),
                    Product.is_active == True,
                    Product.price >= min_p,
                    Product.price <= max_p,
                )
            )
            current_count = result.scalar()

            if current_count == len(cached[0]):
                logger.debug(f"Cache hit for key: {key}")
                return cached[0]
            else:
                logger.debug(f"Cache invalidated for key: {key} due to count mismatch (cached: {len(cached[0])}, current: {current_count})")

    # Fetch fresh data
    async with get_sessionmaker()() as session:
        result = await session.execute(
            select(Product)
            .where(
                Product.category == CategoryEnum(category),
                Product.is_active == True,
                Product.price >= min_p,
                Product.price <= max_p,
            )
            .order_by(Product.id.desc())
        )
        products = result.scalars().all()

    _CATALOG_CACHE[key] = (products, now)
    logger.debug(f"Cache updated for key: {key}")
    return products

# ==========================
# Human-readable helpers
# ==========================

async def get_bot_text(key: str, default: str) -> str:
    async with get_sessionmaker()() as session:
        result = await session.execute(select(BotText).where(BotText.key == key))
        obj = result.scalar_one_or_none()
        return obj.value if obj else default

def delivery_human(delivery_type: str | None) -> str:
    mapping = {
        "pickup": "🏃 Самовывоз",
        "delivery": "🚚 Доставка курьером",
    }
    return mapping.get(delivery_type, "—")


def payment_human(payment_type: str | None) -> str:
    mapping = {
        "cash": "💵 Наличные",
        "transfer": "💸 Перевод",
        "card": "💳 Карта (терминал)",
    }
    return mapping.get(payment_type, "—")


def request_label(req: Request) -> str:
    """Generate human-readable label for a request."""
    dt = req.created_at.strftime("%d.%m.%y %H:%M")
    status = req.status.value.upper()
    return f"№{req.id} от {dt} ({status})"

# ==========================
# Handlers
# ==========================

@router.message(F.text.in_({"/start", "🏠 Главное меню"}))
async def start_handler(m: Message, config: Config):
    """ Handle /start command or 'Main Menu' button.
    Registers/updates user and shows welcome message with menu. """
    async with get_sessionmaker()() as session:
        await upsert_user(
            session=session,
            tg_id=m.from_user.id,
            username=m.from_user.username,
            first_name=m.from_user.first_name,
            admin_ids=config.admin_ids,
        )

    welcome_text = await get_bot_text("start_message", "🌸 Добро пожаловать в Bloom Lavka!")
    is_admin = await is_admin_cached(m.from_user.id)

    await m.answer(welcome_text, reply_markup=kb_start(is_admin))
    # Add persistent bottom menu button
    await m.answer(
        "Чтобы вернуться в главное меню нажмите '🏠 Главное меню' ⬇️",
        reply_markup=kb_main_menu_bottom(),
    )


@router.callback_query(F.data == "support")
async def support_handler(c: CallbackQuery):
    """ Handle support callback.
    Displays support message and returns to main menu. """
    is_admin = await is_admin_cached(c.from_user.id)
    support_text = await get_bot_text(
        "support_message", "❓ Возникли вопросы? Напишите нашему менеджеру."
    )
    await c.message.edit_text(support_text, reply_markup=kb_start(is_admin))
    await c.answer()


@router.callback_query(F.data == "back:start")
async def back_to_start(c: CallbackQuery):
    """ Handle back to start callback.
    Returns to main menu, handling potential message edit failures. """
    is_admin = await is_admin_cached(c.from_user.id)
    text = "🏠 Главное меню"
    markup = kb_start(is_admin=is_admin)
    try:
        await c.message.edit_text(text, reply_markup=markup)
    except Exception:
        try:
            await c.message.delete()
        except Exception:
            pass
        await c.message.answer(text, reply_markup=markup)
    await c.answer()


@router.callback_query(F.data.startswith("cat:"))
async def category_select(c: CallbackQuery):
    """ Handle category selection callback.
    Shows price filters for selected category. """
    cat = c.data.split(":")[1]
    text = "💰 Выберите бюджет:"
    markup = kb_price_filters(cat)
    try:
        await c.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "there is no text" in str(e).lower():
            await c.message.delete()
            await c.message.answer(text, reply_markup=markup)
        else:
            raise
    await c.answer()


@router.callback_query(F.data.startswith("filter:"))
async def filter_select(c: CallbackQuery):
    _, cat, price_data = c.data.split(":")
    if price_data == "all":
        min_p, max_p = 0, 999999
    else:
        a, b = price_data.split("-")
        min_p = int(a)
        max_p = int(b) if b else 999999
    await show_product(c, cat, min_p, max_p, 0, price_data)
    await c.answer()


async def show_product(c: CallbackQuery, category: str, min_p: int, max_p: int, index: int, price_data: str):
    """ Display a product with navigation.
    Handles photo from Telegram file_id, external URL, or local path.
    Falls back to sending new message if edit fails. """
    products = await get_products_cached(category, min_p, max_p)
    if not products:
        await c.answer("😔 Товаров нет", show_alert=True)
        return

    product = products[index]
    text = (
        f"<b>{product.title}</b>\n\n"
        f"{product.description or 'Описание скоро появится'}\n\n"
        f"💰 Цена: <b>{product.price} ₽</b>"
    )
    markup = kb_product_nav(category, price_data, index, len(products), product.id)

    photo = None
    if product.photo_file_id:
        photo = product.photo_file_id
    elif product.image_url:
        if product.image_url.startswith(('http://', 'https://')):
            photo = product.image_url
        else:
            base = Path(__file__).resolve().parent.parent.parent
            path = base / product.image_url.lstrip('/')
            if path.exists() and path.stat().st_size > 0:  # Фикс: проверка на non-empty file
                photo = FSInputFile(path)
            else:
                logger.warning(f"Local image path does not exist or empty: {path}")

    if photo:
        media = InputMediaPhoto(media=photo, caption=text, parse_mode="HTML")
        try:
            if c.message.photo:
                await c.message.edit_media(media=media, reply_markup=markup)
            else:
                await c.message.delete()
                await c.message.answer_photo(photo=photo, caption=text, parse_mode="HTML", reply_markup=markup)
        except Exception as e:
            logger.error(f"Failed to edit/send photo: {e}")
            try:
                await c.message.delete()
            except Exception:
                pass
            await c.message.answer_photo(photo=photo, caption=text, parse_mode="HTML", reply_markup=markup)
    else:
        try:
            await c.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception as e:
            logger.error(f"Failed to edit text: {e}")
            try:
                await c.message.delete()
            except Exception:
                pass
            await c.message.answer(text, parse_mode="HTML", reply_markup=markup)

    await c.answer()


@router.callback_query(F.data.startswith("nav:"))
async def product_nav(c: CallbackQuery):
    _, cat, price_data, idx_str = c.data.split(":")
    index = int(idx_str)
    if price_data == "all":
        min_p, max_p = 0, 999999
    else:
        a, b = price_data.split("-")
        min_p = int(a)
        max_p = int(b) if b else 999999
    await show_product(c, cat, min_p, max_p, index, price_data)
    await c.answer()


@router.callback_query(F.data == "my:req:list")
async def my_requests_list(c: CallbackQuery):
    """ List user's requests with navigation to view each. """
    async with get_sessionmaker()() as session:
        res = await session.execute(
            select(Request)
            .join(Request.user)
            .where(User.tg_id == c.from_user.id)
            .order_by(Request.created_at.desc())
        )
        requests = res.scalars().all()

    if not requests:
        await c.answer("😔 У вас нет заявок", show_alert=True)
        return

    items = [(req.id, request_label(req)) for req in requests]
    await c.message.edit_text("📦 Ваши заявки:", reply_markup=kb_my_requests_list(items))
    await c.answer()


@router.callback_query(F.data.startswith("my:req:view:"))
async def my_request_view(c: CallbackQuery):
    """ View details of a specific user request.
    Allows cancellation if status is NEW. """
    request_id = int(c.data.split(":")[-1])

    async with get_sessionmaker()() as session:
        req = await session.scalar(
            select(Request)
            .options(selectinload(Request.product), selectinload(Request.user))
            .where(Request.id == request_id)
        )

    if not req or req.user.tg_id != c.from_user.id:
        await c.answer("🚫 Нет доступа", show_alert=True)
        return

    price = req.product.price if req.product else 0
    date_str = req.need_datetime.strftime('%d.%m.%Y') if req.need_datetime else '—'

    text = (
        f"📄 <b>Заявка №{req.id}</b>\n\n"
        f"👤 Имя: {req.customer_name or '—'}\n"
        f"💐 Товар: {req.product.title if req.product else '—'}\n"
        f"💰 Сумма: <b>{price} ₽</b>\n"
        f"📅 Дата: {date_str}\n"
        f"📞 Телефон: <code>{req.phone or '—'}</code>\n"
        f"🚚 Получение: {delivery_human(req.delivery_type)}\n"
        f"💳 Оплата: {payment_human(req.payment_type)}\n"
    )

    if req.delivery_type == "delivery":
        text += f"📍 Адрес: {req.address or '—'}\n"

    if req.comment:
        text += f"📝 Комментарий: {req.comment}\n"

    text += f"\n📌 Статус: <b>{req.status.value.upper()}</b>"

    can_cancel = req.status == RequestStatus.NEW
    await c.message.edit_text(text, reply_markup=kb_my_request_view(req.id, can_cancel))
    await c.answer()


@router.callback_query(F.data.startswith("req:start:"))
async def req_start(c: CallbackQuery, state: FSMContext):
    """ Start request FSM for a product.
    Saves product ID and prompts for date. """
    product_id = int(c.data.split(":")[2])
    await state.update_data(product_id=product_id)
    await state.set_state(RequestFSM.need_date)
    await c.message.answer("📅 Укажите дату, на которую нужен букет (формат: 03.03.2026):")
    await c.answer()


@router.message(RequestFSM.need_date)
async def req_need_date(m: Message, state: FSMContext):
    """ Handle date input in FSM. Saves date and prompts for delivery type. """
    try:
        need_datetime = datetime.strptime(m.text.strip(), "%d.%m.%Y")
        await state.update_data(need_datetime=need_datetime)
    except ValueError:
        await m.answer("❌ Неверный формат! Используйте DD.MM.YYYY (например, 03.03.2026).")
        return

    await state.set_state(RequestFSM.delivery_type)
    await m.answer("🚚 Выберите способ получения:", reply_markup=kb_delivery_type())


@router.callback_query(RequestFSM.delivery_type, F.data.startswith("req:delivery:"))
async def req_delivery(c: CallbackQuery, state: FSMContext):
    """ Handle delivery type selection in FSM.
    Saves type and prompts for payment. """
    delivery_type = c.data.split(":")[2]
    await state.update_data(delivery_type=delivery_type)
    await state.set_state(RequestFSM.payment_type)
    text = "💳 Выберите способ оплаты:"
    markup = kb_payment_type(delivery_type)
    try:
        await c.message.edit_text(text, reply_markup=markup)
    except Exception:
        await c.message.answer(text, reply_markup=markup)
    await c.answer()


@router.callback_query(RequestFSM.payment_type, F.data.startswith("req:pay:"))
async def req_payment(c: CallbackQuery, state: FSMContext):
    """ Handle payment type selection in FSM.
    Saves type and prompts for customer name. """
    payment_type = c.data.split(":")[2]
    await state.update_data(payment_type=payment_type)
    await state.set_state(RequestFSM.customer_name)
    await c.message.answer("👤 Как к вам обращаться?")
    await c.answer()


@router.message(RequestFSM.customer_name)
async def req_customer_name(m: Message, state: FSMContext):
    """ Handle customer name input in FSM.
    Saves name and prompts for phone. """
    await state.update_data(customer_name=m.text.strip())
    await state.set_state(RequestFSM.phone)
    await m.answer("📞 Укажите номер телефона:")


@router.message(RequestFSM.phone)
async def req_phone(m: Message, state: FSMContext):
    """ Handle phone input in FSM.
    Saves phone and branches based on delivery type. """
    await state.update_data(phone=m.text.strip())
    data = await state.get_data()
    if data["delivery_type"] == "delivery":
        await state.set_state(RequestFSM.address)
        await m.answer("📍 Укажите адрес доставки:")
    else:
        await state.set_state(RequestFSM.comment)
        await m.answer("📝 Добавить комментарий к заказу?", reply_markup=kb_skip_comment())


@router.message(RequestFSM.address)
async def req_address(m: Message, state: FSMContext):
    """ Handle address input in FSM (for delivery).
    Saves address and prompts for comment. """
    await state.update_data(address=m.text.strip())
    await state.set_state(RequestFSM.comment)
    await m.answer("📝 Добавить комментарий к заказу?", reply_markup=kb_skip_comment())


@router.message(RequestFSM.comment)
async def req_comment(m: Message, state: FSMContext):
    """ Handle comment input in FSM.
    Saves comment and shows confirmation. """
    await state.update_data(comment=m.text.strip())
    await show_confirm(m, state)


@router.callback_query(RequestFSM.comment, F.data == "req:skip_comment")
async def skip_comment_handler(c: CallbackQuery, state: FSMContext):
    """ Handle skip comment callback in FSM.
    Sets empty comment and shows confirmation. """
    await state.update_data(comment=None)
    await show_confirm(c.message, state)
    await c.answer()


async def show_confirm(msg: Message, state: FSMContext):
    """ Display confirmation screen with all FSM data.
    Transitions to confirm state. """
    data = await state.get_data()
    await state.set_state(RequestFSM.confirm)
    date_str = data["need_datetime"].strftime('%d.%m.%Y') if "need_datetime" in data else '—'
    text = (
        f"<b>Проверьте данные:</b>\n\n"
        f"📅 Дата: {date_str}\n"
        f"👤 Имя: {data.get('customer_name', '—')}\n"
        f"📞 Тел: {data.get('phone', '—')}\n"
        f"🚚 Получение: {delivery_human(data.get('delivery_type'))}\n"
        f"📍 Адрес: {data.get('address', '—')}\n"
        f"💳 Оплата: {payment_human(data.get('payment_type'))}\n"
        f"📝 Комментарий: {data.get('comment', '—')}"
    )
    await msg.answer(text, reply_markup=kb_confirm())


@router.callback_query(RequestFSM.confirm, F.data == "req:confirm:yes")
async def req_confirm(c: CallbackQuery, state: FSMContext, config: Config):
    """ Handle confirmation yes in FSM.
    Creates request in DB, notifies admins, clears state. """
    data = await state.get_data()
    async with get_sessionmaker()() as session:
        user = await session.scalar(select(User).where(User.tg_id == c.from_user.id))

        new_request = Request(
            user_id=user.id,
            product_id=data["product_id"],
            customer_name=data.get("customer_name"),
            phone=data.get("phone"),
            delivery_type=data.get("delivery_type"),
            address=data.get("address"),
            payment_type=data.get("payment_type"),
            comment=data.get("comment"),
            need_datetime=data.get("need_datetime"),
            status=RequestStatus.NEW,
        )
        session.add(new_request)
        await session.commit()

    await state.clear()
    await c.message.edit_text(
        "🎉 Заявка отправлена! Мы скоро свяжемся.",
        reply_markup=kb_after_request_sent(),
    )

    # Notify admins
    for admin_id in config.admin_ids:
        try:
            await c.bot.send_message(admin_id, "🆕 Новая заявка!")
        except Exception as e:
            logger.warning(f"Failed to notify admin {admin_id}: {e}")

    await c.answer()


@router.callback_query(F.data == "req:cancel")
async def req_cancel(c: CallbackQuery, state: FSMContext):
    """ Cancel ongoing request FSM.
    Clears state and returns to main menu. """
    await state.clear()
    await back_to_start(c)
    await c.answer("❌ Заявка отменена")
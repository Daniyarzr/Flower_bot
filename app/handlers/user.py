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

@router.message(F.text == "/start")  # Или commands=['start']
async def start_handler(m: Message):
    is_admin = await is_admin_cached(m.from_user.id)
    await m.answer("Добро пожаловать в BLOOM lavka!", reply_markup=kb_start(is_admin))

@router.message()
async def echo(m: Message):
    await m.answer("Команда не распознана. Используйте /start для начала.🌸")

async def get_products_cached(category: str, min_p: int, max_p: int) -> List[Product]:
    """
    Fetch products from database with caching to reduce query load.
    Cache is invalidated after CATALOG_TTL seconds or if product count changes (e.g., due to additions/deletions).

    :param category: Product category (e.g., 'bouquet')
    :param min_p: Minimum price filter
    :param max_p: Maximum price filter
    :return: List of active products matching filters
    """
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

def delivery_human(delivery_type: str | None) -> str:
    mapping = {
        "pickup": "🏃 Самовывоз",
        "delivery": "🚚 Доставка",
    }
    return mapping.get(delivery_type, "—")


def payment_human(payment_type: str | None) -> str:
    mapping = {
        "cash": "💵 Наличные",
        "transfer": "💸 Перевод",
        "card": "💳 Карта",
    }
    return mapping.get(payment_type, "—")


# ==========================
# Back to start
# ==========================

@router.callback_query(F.data == "back:start")
async def back_to_start(c: CallbackQuery):
    is_admin = await is_admin_cached(c.from_user.id)
    await c.message.edit_text("Добро пожаловать в BLOOM lavka!", reply_markup=kb_start(is_admin))
    await c.answer()


# ==========================
# Catalog
# ==========================

@router.callback_query(F.data.startswith("cat:"))
async def show_category(c: CallbackQuery):
    category = c.data.split(":")[1]
    text = "💐 Выберите ценовой диапазон:" if category == "bouquet" else "🌿 Выберите ценовой диапазон:"
    await c.message.edit_text(text, reply_markup=kb_price_filters(category))
    await c.answer()


@router.callback_query(F.data.startswith("filter:"))
async def show_products(c: CallbackQuery):
    parts = c.data.split(":")
    category = parts[1]
    price_data = parts[2]

    if price_data == "all":
        min_p, max_p = 0, 999999
    else:
        min_str, max_str = price_data.split("-")
        min_p = int(min_str)
        max_p = int(max_str) if max_str != "0" else 999999

    products = await get_products_cached(category, min_p, max_p)

    if not products:
        await c.answer("😔 Товары не найдены", show_alert=True)
        return

    await show_product(c.message, category, price_data, 0, products)
    await c.answer()


async def show_product(msg: Message, category: str, price_data: str, index: int, products: List[Product]):
    p = products[index]
    text = f"<b>{p.title}</b>\n\n{p.description or ''}\n\n💰 {p.price} ₽"

    photo_id = p.photo_file_id or p.image_url
    if not photo_id:
        await msg.edit_text(text, reply_markup=kb_product_nav(category, price_data, index, len(products), p.id))
        return

    media = InputMediaPhoto(media=photo_id, caption=text)
    try:
        await msg.edit_media(media=media, reply_markup=kb_product_nav(category, price_data, index, len(products), p.id))
    except TelegramBadRequest:
        await msg.answer_photo(photo=photo_id, caption=text, reply_markup=kb_product_nav(category, price_data, index, len(products), p.id))


@router.callback_query(F.data.startswith("nav:"))
async def nav_product(c: CallbackQuery):
    parts = c.data.split(":")
    category = parts[1]
    price_data = parts[2]
    index = int(parts[3])

    products = await get_products_cached(
        category,
        int(price_data.split("-")[0]) if "-" in price_data else 0,
        int(price_data.split("-")[1]) if "-" in price_data and price_data.split("-")[1] != "0" else 999999
    )

    await show_product(c.message, category, price_data, index, products)
    await c.answer()


# ==========================
# Request FSM
# ==========================

@router.callback_query(F.data.startswith("req:start:"))
async def req_start(c: CallbackQuery, state: FSMContext):
    product_id = int(c.data.split(":")[-1])
    await state.set_state(RequestFSM.need_date)
    await state.set_data({"product_id": product_id})
    await c.message.answer("📅 Укажите дату, на которую нужен букет (формат: 03.03.2026):", reply_markup=kb_main_menu_bottom())
    await c.answer()


@router.message(RequestFSM.need_date)
async def req_need_date(m: Message, state: FSMContext):
    try:
        need_date = datetime.strptime(m.text.strip(), "%d.%m.%Y")
    except ValueError:
        await m.answer("❌ Неверный формат даты. Пожалуйста, используйте DD.MM.YYYY (например, 03.03.2026).")
        return

    data = await state.get_data()
    data["need_date"] = need_date.strftime("%d.%m.%Y")
    await state.set_data(data)
    await state.set_state(RequestFSM.delivery_type)
    await m.answer("🚚 Выберите способ получения:", reply_markup=kb_delivery_type())


@router.callback_query(RequestFSM.delivery_type, F.data.startswith("req:delivery_type:"))
async def req_delivery_type(c: CallbackQuery, state: FSMContext):
    delivery_type = c.data.split(":")[-1]
    await state.update_data(delivery_type=delivery_type)

    if delivery_type == "delivery":
        await state.set_state(RequestFSM.address)
        await c.message.answer("📍 Укажите адрес доставки:")
    else:
        await state.set_state(RequestFSM.payment_type)
        await c.message.edit_text("💳 Выберите способ оплаты:", reply_markup=kb_payment_type(delivery_type))
    await c.answer()


@router.message(RequestFSM.address)
async def req_address(m: Message, state: FSMContext):
    await state.update_data(address=m.text.strip())
    data = await state.get_data()
    await state.set_state(RequestFSM.payment_type)
    await m.answer("💳 Выберите способ оплаты:", reply_markup=kb_payment_type(data["delivery_type"]))


@router.callback_query(RequestFSM.payment_type, F.data.startswith("req:payment_type:"))
async def req_payment_type(c: CallbackQuery, state: FSMContext):
    payment_type = c.data.split(":")[-1]
    await state.update_data(payment_type=payment_type)
    await state.set_state(RequestFSM.customer_name)
    await c.message.answer("👤 Укажите ваше имя:")
    await c.answer()


@router.message(RequestFSM.customer_name)
async def req_customer_name(m: Message, state: FSMContext):
    await state.update_data(customer_name=m.text.strip())
    await state.set_state(RequestFSM.phone)
    await m.answer("📞 Укажите номер телефона:")


@router.message(RequestFSM.phone)
async def req_phone(m: Message, state: FSMContext):
    await state.update_data(phone=m.text.strip())
    await state.set_state(RequestFSM.comment)
    await m.answer("📝 Добавьте комментарий (опционально):", reply_markup=kb_skip_comment())


@router.message(RequestFSM.comment)
async def req_comment(m: Message, state: FSMContext):
    await state.update_data(comment=m.text.strip())
    await show_confirm(m, state)


@router.callback_query(RequestFSM.comment, F.data == "req:skip_comment")
async def skip_comment_handler(c: CallbackQuery, state: FSMContext):
    await state.update_data(comment=None)
    await show_confirm(c.message, state)
    await c.answer()


async def show_confirm(msg: Message, state: FSMContext):
    """
    Display confirmation screen with all FSM data.
    Transitions to confirm state.
    """
    data = await state.get_data()
    await state.set_state(RequestFSM.confirm)

    text = (
        f"<b>Проверьте данные:</b>\n\n"
        f"📅 Дата: {data.get('need_date', '—')}\n"
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
    """
    Handle confirmation yes in FSM.
    Creates request in DB, notifies admins, clears state.
    """
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
            need_datetime=datetime.strptime(data["need_date"], "%d.%m.%Y") if "need_date" in data else None,
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
    """
    Cancel ongoing request FSM.
    Clears state and returns to main menu.
    """
    await state.clear()
    await back_to_start(c)
    await c.answer("❌ Заявка отменена")
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
from app.models import BotText, CategoryEnum, Product, Request, RequestStatus, User
from app.states import RequestFSM
from app.utils import is_admin_cached

router = Router(name="user_router")
logger = logging.getLogger(__name__)

# ========================== КЭШ КАТАЛОГА ==========================
_CATALOG_CACHE: Dict[Tuple[str, int, int], Tuple[List[Product], float]] = {}
CATALOG_TTL = 300

async def get_products_cached(category: str, min_p: int, max_p: int) -> List[Product]:
    key = (category, min_p, max_p)
    now = time()

    cached = _CATALOG_CACHE.get(key)
    if cached and now - cached[1] < CATALOG_TTL:
        return cached[0]

    async with get_sessionmaker()() as session:
        result = await session.execute(
            select(Product)
            .where(
                Product.category == CategoryEnum(category),
                Product.is_active == True,
                Product.price >= min_p,
                Product.price <= max_p,
            )
            .order_by(Product.price)
        )
        products = result.scalars().all()

    _CATALOG_CACHE[key] = (products, now)
    return products


# ========================== ПОМОЩНИКИ ==========================
async def get_bot_text(key: str, default: str) -> str:
    async with get_sessionmaker()() as session:
        result = await session.execute(select(BotText).where(BotText.key == key))
        obj = result.scalar_one_or_none()
        return obj.value if obj else default


def delivery_human(delivery_type: Optional[str]) -> str:
    return {"pickup": "🏃 Самовывоз", "delivery": "🚚 Доставка"}.get(delivery_type, "—")


def payment_human(payment_type: Optional[str]) -> str:
    return {"cash": "💵 Наличные", "transfer": "💸 Перевод", "card": "💳 Карта"}.get(payment_type, "—")


# ========================== СТАРТ ==========================
@router.message(F.text.in_({"/start", "🏠 Главное меню"}))
async def start_handler(m: Message, config: Config):
    async with get_sessionmaker()() as session:
        await upsert_user(
            session=session,
            tg_id=m.from_user.id,
            username=m.from_user.username,
            first_name=m.from_user.first_name,
            admin_ids=config.admin_ids,
        )

    welcome_text = await get_bot_text("start_message", "🌸 Добро пожаловать в BLOOM lavka!")
    is_admin = await is_admin_cached(m.from_user.id)

    await m.answer(welcome_text, reply_markup=kb_start(is_admin))
    await m.answer(
        "Чтобы вернуться в главное меню нажмите '🏠 Главное меню' ⬇️",
        reply_markup=kb_main_menu_bottom(),
    )


# ========================== КАТАЛОГ ==========================
@router.callback_query(F.data.startswith("cat:"))
async def category_select(c: CallbackQuery):
    cat = c.data.split(":")[1]
    await c.message.edit_text("💰 Выберите бюджет:", reply_markup=kb_price_filters(cat))
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
    products = await get_products_cached(category, min_p, max_p)
    if not products:
        await c.answer("😔 Товаров нет", show_alert=True)
        return

    product = products[index]
    text = f"<b>{product.title}</b>\n\n{product.description or 'Описание скоро появится'}\n\n💰 Цена: <b>{product.price} ₽</b>"

    markup = kb_product_nav(category, price_data, index, len(products), product.id)

    # === ИСПРАВЛЕНИЕ ОШИБКИ С ФОТО ===
    photo = None
    if product.photo_file_id:
        photo = product.photo_file_id
    elif product.image_url:
        if product.image_url.startswith(("http://", "https://")):
            photo = product.image_url
        else:
            base = Path(__file__).resolve().parent.parent.parent
            path = base / product.image_url.lstrip("/")
            if path.exists():
                photo = FSInputFile(path)

    if photo:
        media = InputMediaPhoto(media=photo, caption=text, parse_mode="HTML")
        try:
            if c.message.photo:
                await c.message.edit_media(media=media, reply_markup=markup)
            else:
                await c.message.delete()
                await c.message.answer_photo(photo=photo, caption=text, parse_mode="HTML", reply_markup=markup)
        except Exception:
            await c.message.answer_photo(photo=photo, caption=text, parse_mode="HTML", reply_markup=markup)
    else:
        try:
            await c.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
        except Exception:
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


# ========================== ОФОРМЛЕНИЕ ЗАКАЗА ==========================
@router.callback_query(F.data.startswith("req:start:"))
async def req_start(c: CallbackQuery, state: FSMContext):
    product_id = int(c.data.split(":")[2])
    await state.update_data(product_id=product_id)
    await state.set_state(RequestFSM.need_date)
    await c.message.answer("📅 Укажите дату, на которую нужен букет (формат: 03.03.2026):")
    await c.answer()


@router.message(RequestFSM.need_date)
async def req_need_date(m: Message, state: FSMContext):
    try:
        dt = datetime.strptime(m.text.strip(), "%d.%m.%Y")
        await state.update_data(need_date=dt)
        await state.set_state(RequestFSM.delivery_type)
        await m.answer("🚚 Выберите способ получения:", reply_markup=kb_delivery_type())
    except ValueError:
        await m.answer("❌ Неверный формат! Используйте DD.MM.YYYY (например 03.03.2026)")


@router.callback_query(RequestFSM.delivery_type, F.data.startswith("req:delivery:"))
async def req_delivery(c: CallbackQuery, state: FSMContext):
    delivery_type = c.data.split(":")[2]
    await state.update_data(delivery_type=delivery_type)
    await state.set_state(RequestFSM.payment_type)
    await c.message.edit_text(
        "💳 Выберите способ оплаты:",
        reply_markup=kb_payment_type(delivery_type)   # динамическая клавиатура
    )
    await c.answer()


@router.callback_query(RequestFSM.payment_type, F.data.startswith("req:pay:"))
async def req_payment(c: CallbackQuery, state: FSMContext):
    payment_type = c.data.split(":")[2]
    await state.update_data(payment_type=payment_type)
    await state.set_state(RequestFSM.customer_name)
    await c.message.answer("👤 Как к вам обращаться?")


@router.message(RequestFSM.customer_name)
async def req_customer_name(m: Message, state: FSMContext):
    await state.update_data(customer_name=m.text.strip())
    await state.set_state(RequestFSM.phone)
    await m.answer("📞 Укажите номер телефона:")


@router.message(RequestFSM.phone)
async def req_phone(m: Message, state: FSMContext):
    await state.update_data(phone=m.text.strip())
    data = await state.get_data()
    if data["delivery_type"] == "delivery":
        await state.set_state(RequestFSM.address)
        await m.answer("📍 Укажите адрес доставки:")
    else:
        await state.set_state(RequestFSM.comment)
        await m.answer("📝 Добавить комментарий?", reply_markup=kb_skip_comment())


@router.message(RequestFSM.address)
async def req_address(m: Message, state: FSMContext):
    await state.update_data(address=m.text.strip())
    await state.set_state(RequestFSM.comment)
    await m.answer("📝 Добавить комментарий?", reply_markup=kb_skip_comment())


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
    data = await state.get_data()
    await state.set_state(RequestFSM.confirm)
    text = (
        f"<b>Проверьте данные:</b>\n\n"
        f"📅 Дата: {data.get('need_date').strftime('%d.%m.%Y')}\n"
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
    data = await state.get_data()
    async with get_sessionmaker()() as session:
        user = await session.scalar(select(User).where(User.tg_id == c.from_user.id))
        req = Request(
            user_id=user.id,
            product_id=data["product_id"],
            customer_name=data.get("customer_name"),
            phone=data.get("phone"),
            delivery_type=data.get("delivery_type"),
            address=data.get("address"),
            payment_type=data.get("payment_type"),
            comment=data.get("comment"),
            need_datetime=data.get("need_date"),
            status=RequestStatus.NEW,
        )
        session.add(req)
        await session.commit()

    await state.clear()
    await c.message.edit_text("🎉 Заявка отправлена! Мы скоро свяжемся.", reply_markup=kb_after_request_sent())

    for admin_id in config.admin_ids:
        try:
            await c.bot.send_message(admin_id, "🆕 Новая заявка!")
        except Exception:
            pass
    await c.answer()


@router.callback_query(F.data == "req:cancel")
async def req_cancel(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("❌ Заявка отменена")
    await c.answer()
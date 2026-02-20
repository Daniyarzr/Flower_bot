from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import get_sessionmaker
from app.keyboards import kb_admin_panel
from app.models import Request, RequestStatus
from app.utils import is_admin_cached, tg_user_link

router = Router()

# ==========================
# Helper Functions (assuming delivery_human and payment_human are defined in utils.py or here)
# ==========================
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


def kb_admin_request_view(request_id: int, status: RequestStatus) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    if status == RequestStatus.NEW:
        builder.button(text="🛠 Взять в работу", callback_data=f"admin:req:status:in_work:{request_id}")
    
    if status != RequestStatus.DONE:
        builder.button(text="✅ Завершить", callback_data=f"admin:req:status:done:{request_id}")
    
    builder.button(text="❌ Отменить", callback_data=f"admin:req:status:canceled:{request_id}")
    
    # Кнопка назад к списку (можно динамически менять на основе статуса, но для простоты общая)
    builder.button(text="⬅ К списку", callback_data=f"admin:req:list:{status.value}")
    
    builder.adjust(1)
    return builder.as_markup()


# ==========================
# Админ-панель
# ==========================

@router.callback_query(F.data == "admin:panel")
async def admin_panel(c: CallbackQuery):
    if not await is_admin_cached(c.from_user.id):
        return await c.answer("🚫 Нет доступа", show_alert=True)
    
    await c.message.edit_text(
        "🛠 Админ-панель",
        reply_markup=kb_admin_panel()
    )
    await c.answer()


# ==========================
# Списки заявок по статусам
# ==========================

@router.callback_query(F.data.startswith("admin:req:list:"))
async def admin_requests_list(c: CallbackQuery):
    if not await is_admin_cached(c.from_user.id):
        return await c.answer("🚫 Нет доступа", show_alert=True)
    
    status_str = c.data.split(":")[-1]
    try:
        status = RequestStatus(status_str)
    except ValueError:
        return await c.answer("🚫 Неверный статус", show_alert=True)
    
    async with get_sessionmaker()() as session:
        res = await session.execute(
            select(Request)
            .where(Request.status == status)
            .order_by(Request.created_at.desc())
        )
        requests = res.scalars().all()
    
    if not requests:
        return await c.answer("😔 Нет заявок", show_alert=True)
    
    builder = InlineKeyboardBuilder()
    for req in requests:
        dt = req.created_at.strftime("%d.%m.%y %H:%M")
        label = f"№{req.id} от {dt}"
        builder.button(
            text=label,
            callback_data=f"admin:req:view:{req.id}"
        )
    
    builder.button(text="⬅ В панель", callback_data="admin:panel")
    builder.adjust(1)
    
    await c.message.edit_text(
        f"📋 Заявки: {status.value.upper()}",
        reply_markup=builder.as_markup()
    )
    await c.answer()


# ==========================
# Просмотр заявки
# ==========================

@router.callback_query(F.data.startswith("admin:req:view:"))
async def admin_request_view(c: CallbackQuery):
    if not await is_admin_cached(c.from_user.id):
        return await c.answer("🚫 Нет доступа", show_alert=True)
    
    request_id = int(c.data.split(":")[-1])
    
    async with get_sessionmaker()() as session:
        req = await session.scalar(
            select(Request)
            .options(selectinload(Request.product), selectinload(Request.user))
            .where(Request.id == request_id)
        )
    
    if not req:
        return await c.answer("😔 Заявка не найдена", show_alert=True)
    
    price = req.product.price if req.product else 0
    
    text = (
        f"📄 <b>Заявка №{req.id}</b>\n\n"
        f"👤 Имя: {req.customer_name or '—'}\n"
        f"📞 Телефон: <code>{req.phone or '—'}</code>\n"
        f"💐 Товар: {req.product.title if req.product else '—'}\n"
        f"💰 Сумма: <b>{price} ₽</b>\n"
        f"🚚 Получение: {delivery_human(req.delivery_type)}\n"
    )
    
    if req.delivery_type == "delivery":
        text += f"📍 Адрес: {req.address or '—'}\n"
    
    text += (
        f"💳 Оплата: {payment_human(req.payment_type)}\n"
        f"📝 Комментарий: {req.comment or '—'}\n\n"
        f"📌 Статус: <b>{req.status.value.upper()}</b>\n"
        f"🕒 Создано: {req.created_at.strftime('%d.%m.%y %H:%M')}\n\n"
        f"👥 Клиент: {tg_user_link(req.user.tg_id, req.user.username)}"
    )
    
    await c.message.edit_text(
        text,
        reply_markup=kb_admin_request_view(req.id, req.status)
    )
    await c.answer()


# ==========================
# Изменение статуса
# ==========================

@router.callback_query(F.data.startswith("admin:req:status:"))
async def admin_change_status(c: CallbackQuery):
    if not await is_admin_cached(c.from_user.id):
        return await c.answer("🚫 Нет доступа", show_alert=True)
    
    parts = c.data.split(":")
    new_status_str = parts[3]
    request_id = int(parts[4])
    
    try:
        new_status = RequestStatus(new_status_str)
    except ValueError:
        return await c.answer("🚫 Неверный статус", show_alert=True)
    
    async with get_sessionmaker()() as session:
        req = await session.scalar(select(Request).where(Request.id == request_id))
        if not req:
            return await c.answer("😔 Заявка не найдена", show_alert=True)
        
        old_status = req.status
        req.status = new_status
        await session.commit()
    
    await c.answer(f"✅ Статус изменён на {new_status.value.upper()}")
    
    # Обновляем вид заявки
    await admin_request_view(c)
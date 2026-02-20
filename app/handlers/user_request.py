from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import get_sessionmaker
from app.models import Request, RequestStatus, DeliveryType
from app.keyboards import kb_my_request_view

router = Router()


def delivery_type_human(value: DeliveryType | None) -> str:
    return {
        DeliveryType.DELIVERY: "🚚 Доставка",
        DeliveryType.PICKUP: "🏃 Самовывоз",
    }.get(value, "—")


@router.callback_query(F.data.startswith("my:req:view:"))
async def my_request_view(c: CallbackQuery):
    request_id = int(c.data.split(":")[-1])

    async with get_sessionmaker()() as session:
        res = await session.execute(
            select(Request)
            .options(
                selectinload(Request.user),
                selectinload(Request.product)
            )
            .where(Request.id == request_id)
        )
        req = res.scalar_one_or_none()

    if not req or not req.user or req.user.tg_id != c.from_user.id:
        await c.answer("🚫 Доступ запрещён", show_alert=True)
        return

    product_title = req.product.title if req.product else "—"
    price = req.product.price if req.product else "—"

    text = (
        f"📄 <b>Заявка №{req.id}</b>\n\n"
        f"💐 Товар: {product_title}\n"
        f"💰 Цена: {price} ₽\n"
        f"📞 Телефон: <code>{req.phone}</code>\n"
        f"🚚 Способ получения: {delivery_human(req.delivery_type)}\n"
        f"📍 Адрес: {req.address}\n"
        f"\n📌 Статус: <b>{req.status.value}</b>"
    )


    if req.address:
        text += f"📍 Адрес: {req.address}\n"

    if req.comment:
        text += f"📝 Комментарий: {req.comment}\n"

    text += f"\n📌 Статус: <b>{req.status.value}</b>"

    await c.message.edit_text(
        text,
        reply_markup=kb_my_request_view(
            request_id=req.id,
            can_cancel=req.status == RequestStatus.NEW
        )
    )
    await c.answer()

def delivery_human(delivery_type: str) -> str:
    return {
        "pickup": "🏃 Самовывоз",
        "delivery": "🚚 Доставка курьером",
    }.get(delivery_type, "—")

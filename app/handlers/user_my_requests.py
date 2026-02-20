from aiogram import Router, F
from aiogram.types import CallbackQuery
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload

from app.db import get_sessionmaker
from app.models import Request, RequestStatus
from app.keyboards import kb_my_requests_list, kb_start

router = Router()


@router.callback_query(F.data == "my:req:list")
async def my_requests_list(c: CallbackQuery):
    """
    Показывает последние 10 заявок текущего пользователя.
    """
    async with get_sessionmaker()() as session:
        res = await session.execute(
            select(Request)
            .options(
                selectinload(Request.product),
                selectinload(Request.user)
            )
            .where(Request.user.has(tg_id=c.from_user.id))
            .order_by(desc(Request.created_at))
            .limit(10)
        )
        requests = res.scalars().all()

    if not requests:
        await c.message.edit_text(
            "📭 <b>У вас пока нет заявок</b>",
            reply_markup=kb_start()
        )
        await c.answer()
        return

    items = []
    for r in requests:
        status_icon = {
            RequestStatus.NEW: "🆕",
            RequestStatus.IN_WORK: "🛠",
            RequestStatus.DONE: "✅",
            RequestStatus.CANCELED: "❌",
        }.get(r.status, "📦")

        price = r.product.price if r.product else 0
        label = f"{status_icon} {r.customer_name} · {price} ₽"
        items.append((r.id, label))


    await c.message.edit_text(
        "📦 <b>Мои заявки</b>\n\nВыберите заявку для просмотра:",
        reply_markup=kb_my_requests_list(items)
    )
    await c.answer()

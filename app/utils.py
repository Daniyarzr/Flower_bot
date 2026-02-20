from typing import Optional
from time import time

from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.db import get_sessionmaker
from app.models import User, UserRole


# =========================
# 🔐 Safe edit (PRODUCTION)
# =========================

async def safe_edit(
    event: CallbackQuery | Message,
    text: str,
    reply_markup=None,
    **kwargs
):
    try:
        if isinstance(event, CallbackQuery):
            await event.message.edit_text(
                text,
                reply_markup=reply_markup,
                **kwargs
            )
        else:
            await event.edit_text(
                text,
                reply_markup=reply_markup,
                **kwargs
            )
    except Exception:
        try:
            if isinstance(event, CallbackQuery):
                await event.message.answer(
                    text,
                    reply_markup=reply_markup,
                    **kwargs
                )
            else:
                await event.answer(
                    text,
                    reply_markup=reply_markup,
                    **kwargs
                )
        except Exception:
            pass


# =========================
# 👤 Telegram user link
# =========================

def tg_user_link(tg_id: int, username: Optional[str] = None) -> str:
    if username:
        return f"<a href='https://t.me/{username}'>@{username}</a>"
    return f"<a href='tg://user?id={tg_id}'>Открыть профиль</a>"


# =========================
# ⚡ Async-safe admin cache
# =========================

_ADMIN_CACHE: dict[int, tuple[bool, float]] = {}
ADMIN_CACHE_TTL = 300  # 5 минут


async def is_admin_cached(tg_id: int) -> bool:
    now = time()

    cached = _ADMIN_CACHE.get(tg_id)
    if cached and now - cached[1] < ADMIN_CACHE_TTL:
        return cached[0]

    async with get_sessionmaker()() as session:
        res = await session.execute(
            select(User.id).where(
                User.tg_id == tg_id,
                User.role == UserRole.ADMIN
            )
        )
        is_admin = res.scalar_one_or_none() is not None

    _ADMIN_CACHE[tg_id] = (is_admin, now)
    return is_admin

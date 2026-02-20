from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select, exists, func
from app.models import Base, User, UserRole

_engine = None
_sessionmaker = None

def init_engine(db_url: str):
    global _engine, _sessionmaker
    _engine = create_async_engine(
        db_url,
        pool_size=20,          # Увеличено для нагрузки
        max_overflow=10,
        pool_recycle=1800,
        pool_pre_ping=True,    # Проверка "живости" коннекта (важно для Neon)
        connect_args={"command_timeout": 60}
    )
    _sessionmaker = async_sessionmaker(
        _engine, 
        expire_on_commit=False, 
        class_=AsyncSession
    )

async def create_tables():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        raise RuntimeError("DB Engine not initialized")
    return _sessionmaker

async def is_admin(session: AsyncSession, tg_id: int) -> bool:
    stmt = select(exists().where(User.tg_id == tg_id, User.role == UserRole.ADMIN))
    res = await session.execute(stmt)
    return res.scalar()

async def upsert_user(
    session: AsyncSession, 
    tg_id: int, 
    username: str | None, 
    first_name: str | None, 
    admin_ids: set[int]
) -> User:
    stmt = select(User).where(User.tg_id == tg_id)
    res = await session.execute(stmt)
    user = res.scalar_one_or_none()

    if user:
        user.username = username
        user.first_name = first_name
    else:
        role = UserRole.ADMIN if tg_id in admin_ids else UserRole.USER
        user = User(tg_id=tg_id, username=username, first_name=first_name, role=role)
        session.add(user)
    
    await session.commit()
    return user
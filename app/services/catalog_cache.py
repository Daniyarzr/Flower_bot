from time import time
from sqlalchemy import select
from app.db import get_sessionmaker
from app.models import Product

_CACHE = {
    "data": None,
    "ts": 0
}

TTL = 300  # 5 минут


async def get_catalog():
    now = time()

    if _CACHE["data"] and now - _CACHE["ts"] < TTL:
        return _CACHE["data"]

    Session = get_sessionmaker()
    async with Session() as session:
        res = await session.execute(
            select(Product).order_by(Product.id)
        )
        products = res.scalars().all()

    _CACHE["data"] = products
    _CACHE["ts"] = now
    return products


def drop_catalog_cache():
    _CACHE["data"] = None
    _CACHE["ts"] = 0

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.config import load_config
from app.db import init_engine, create_tables
from app.handlers import routers # Теперь берем из __init__.py

from app.handlers import routers

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

async def main():
    setup_logging()
    logging.info("🚀 Бот запускается...")

    config = load_config()

    # Инициализация БД
    init_engine(config.db_url)
    await create_tables()
    # seed_products() УБРАНО, так как товары добавляются извне

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # Хранилище состояний
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    # Прокидываем конфиг внутрь хендлеров (чтобы доставать айди админов)
    dp["config"] = config

    # Регистрируем роутеры
    for router in routers:
        dp.include_router(router)

    logging.info("👂 Начинаю polling...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
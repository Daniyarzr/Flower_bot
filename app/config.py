from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    bot_token: str
    admin_ids: set[int]
    db_url: str
    # Публичные настройки бота (для красивых текстов)
    shop_name: str
    support_contact: str
    shop_address: str
    work_hours: str
    currency: str


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is empty in .env")

    admin_raw = os.getenv("ADMIN_IDS", "").strip()
    admin_ids = set()
    if admin_raw:
        admin_ids = {int(x.strip()) for x in admin_raw.split(",") if x.strip().isdigit()}

    db_url = os.getenv("DB_URL", "").strip()
    if not db_url:
        raise RuntimeError(
            "DB_URL is empty in .env. Пример: DB_URL=postgresql+asyncpg://user:password@localhost:5432/flower_bot"
        )

    # Эти поля необязательны, но сильно улучшают UX.
    shop_name = os.getenv("SHOP_NAME", "BLOOM lavka").strip() or "BLOOM lavka"
    support_contact = os.getenv("SUPPORT_CONTACT", "@tizhel").strip() or "@tizhel"
    shop_address = os.getenv("SHOP_ADDRESS", "").strip()
    work_hours = os.getenv("WORK_HOURS", "").strip()
    currency = os.getenv("CURRENCY", "₽").strip() or "₽"

    return Config(
        bot_token=token,
        admin_ids=admin_ids,
        db_url=db_url,
        shop_name=shop_name,
        support_contact=support_contact,
        shop_address=shop_address,
        work_hours=work_hours,
        currency=currency,
    )
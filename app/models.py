from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    BigInteger, String, DateTime, Integer, ForeignKey,
    Text, Boolean, Enum as SQLEnum
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy import Text, DateTime, func

from sqlalchemy import (
    Column,
    DateTime,
)
from sqlalchemy.orm import DeclarativeBase, relationship

class Base(DeclarativeBase):
    pass


# ======================
# Enums
# ======================

class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"


class CategoryEnum(str, Enum):
    BOUQUET = "bouquet"
    COMPOSITION = "composition"


class DeliveryType(str, Enum):
    PICKUP = "pickup"
    DELIVERY = "delivery"


class PaymentType(str, Enum):
    CASH = "cash"
    TRANSFER = "transfer"
    CARD = "card"


class RequestStatus(str, Enum):
    NEW = "new"
    IN_WORK = "in_work"
    DONE = "done"
    CANCELED = "canceled"


# ======================
# User
# ======================

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(64))
    first_name: Mapped[Optional[str]] = mapped_column(String(64))
    phone: Mapped[Optional[str]] = mapped_column(String(32))
    role: Mapped[UserRole] = mapped_column(SQLEnum(UserRole), default=UserRole.USER, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    requests: Mapped[list["Request"]] = relationship("Request", back_populates="user", cascade="all, delete-orphan")


# ======================
# Product
# ======================

class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, default="")
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    
    # Исправлено: поддержка и file_id (внутри ТГ) и внешних ссылок (из админки)
    photo_file_id: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    image_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    category: Mapped[CategoryEnum] = mapped_column(SQLEnum(CategoryEnum), nullable=False)

    requests: Mapped[list["Request"]] = relationship("Request", back_populates="product", cascade="all, delete-orphan")


# ======================
# Request (заявка)
# ======================

class Request(Base):
    __tablename__ = "requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), nullable=True)
    
    status: Mapped[RequestStatus] = mapped_column(SQLEnum(RequestStatus), default=RequestStatus.NEW, nullable=False)
    
    customer_name: Mapped[Optional[str]] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(32))
    
    delivery_type: Mapped[Optional[DeliveryType]] = mapped_column(SQLEnum(DeliveryType))
    address: Mapped[Optional[str]] = mapped_column(String(512))
    
    payment_type: Mapped[Optional[PaymentType]] = mapped_column(SQLEnum(PaymentType))
    comment: Mapped[Optional[str]] = mapped_column(Text)
    
    need_datetime: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="requests")
    product: Mapped["Product"] = relationship("Product", back_populates="requests")

    from sqlalchemy import Text, DateTime, func

class SupportMessage(Base):
    __tablename__ = "support_message"

    id = Column(Integer, primary_key=True)
    text = Column(Text, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )

class BotText(Base):
    __tablename__ = "bot_texts"

    key = Column(String, primary_key=True)  
    value = Column(Text, nullable=False)

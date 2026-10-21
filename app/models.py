"""Database tables: users, clients, invoices and messages."""
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

# Invoice statuses
PENDING = "pending"
OVERDUE = "overdue"
PAID = "paid"
PAUSED = "paused"
DISPUTED = "disputed"
OPEN_STATUSES = (PENDING, OVERDUE)  # statuses the agent may chase
UNPAID_STATUSES = (PENDING, OVERDUE, PAUSED, DISPUTED)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; treat them as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    business_name: Mapped[str] = mapped_column(String(160), default="")
    require_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    clients: Mapped[list["Client"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    invoices: Mapped[list["Invoice"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    whatsapp_number: Mapped[str] = mapped_column(String(20), index=True)
    preferred_tone: Mapped[str] = mapped_column(String(20), default="neutral")  # friendly|neutral|formal
    opted_out: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="clients")
    invoices: Mapped[list["Invoice"]] = relationship(back_populates="client", cascade="all, delete-orphan")
    messages: Mapped[list["Message"]] = relationship(back_populates="client", cascade="all, delete-orphan")


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("user_id", "invoice_number", name="uq_user_invoice_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    invoice_number: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(255), default="")
    amount: Mapped[float] = mapped_column(Float)  # in rupees
    issue_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    payment_link: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(20), default=PENDING, index=True)

    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    first_reminder_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_reminder_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_reminder_on: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="invoices")
    client: Mapped[Client] = relationship(back_populates="invoices")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="Message.id"
    )


class Message(Base):
    """Every message sent to or received from a client (the audit trail)."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    direction: Mapped[str] = mapped_column(String(3))  # out | in
    kind: Mapped[str] = mapped_column(String(20), default="reminder")  # reminder | ack | reply
    body: Mapped[str] = mapped_column(Text)
    tone: Mapped[str] = mapped_column(String(20), default="")
    stage: Mapped[int] = mapped_column(Integer, default=0)
    # out: pending_approval | sent | delivered | read | failed | rejected ; in: received
    status: Mapped[str] = mapped_column(String(20), default="sent")
    intent: Mapped[str] = mapped_column(String(20), default="")
    provider_message_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    invoice: Mapped[Invoice] = relationship(back_populates="messages")
    client: Mapped[Client] = relationship(back_populates="messages")

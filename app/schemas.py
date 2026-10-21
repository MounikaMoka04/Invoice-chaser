"""Pydantic request/response models."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .utils import normalize_phone

Tone = Literal["friendly", "neutral", "formal"]


# ---- auth -----------------------------------------------------------------
class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    business_name: str = ""


class UserUpdate(BaseModel):
    name: str | None = None
    business_name: str | None = None
    require_approval: bool | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str
    business_name: str
    require_approval: bool


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---- clients --------------------------------------------------------------
def _check_phone(v: str) -> str:
    digits = normalize_phone(v)
    if not 11 <= len(digits) <= 15:
        raise ValueError("Enter a valid WhatsApp number with country code, e.g. 919876543210")
    return digits


class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    whatsapp_number: str
    preferred_tone: Tone = "neutral"

    @field_validator("whatsapp_number")
    @classmethod
    def _phone(cls, v: str) -> str:
        return _check_phone(v)


class ClientUpdate(BaseModel):
    name: str | None = None
    whatsapp_number: str | None = None
    preferred_tone: Tone | None = None
    opted_out: bool | None = None

    @field_validator("whatsapp_number")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        return None if v is None else _check_phone(v)


class ClientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    whatsapp_number: str
    preferred_tone: str
    opted_out: bool


# ---- invoices -------------------------------------------------------------
class InvoiceCreate(BaseModel):
    client_id: int
    invoice_number: str = Field(min_length=1, max_length=60)
    amount: float = Field(gt=0)
    issue_date: date
    due_date: date
    payment_link: str = ""
    description: str = ""


class InvoiceUpdate(BaseModel):
    amount: float | None = Field(default=None, gt=0)
    due_date: date | None = None
    payment_link: str | None = None
    description: str | None = None
    # Actions: mark_paid, pause, resume, dispute, clear_review
    action: Literal["mark_paid", "pause", "resume", "dispute", "clear_review"] | None = None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    direction: str
    kind: str
    body: str
    tone: str
    stage: int
    status: str
    intent: str
    created_at: datetime


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    client_id: int
    invoice_number: str
    description: str
    amount: float
    issue_date: date
    due_date: date
    payment_link: str
    status: str
    reminder_count: int
    first_reminder_on: date | None
    last_reminder_on: date | None
    next_reminder_on: date | None
    paid_on: date | None
    needs_review: bool
    note: str
    client_name: str = ""


class InvoiceDetail(InvoiceOut):
    messages: list[MessageOut] = []


class RemindResult(BaseModel):
    outcome: str
    reason: str = ""
    message_id: int | None = None


class RunSummary(BaseModel):
    checked: int
    outcomes: dict[str, int]


class ImportResult(BaseModel):
    created: int
    skipped: int
    errors: list[str]

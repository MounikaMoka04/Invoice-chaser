import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..agent.graph import run_invoice
from ..agent.runner import run_daily_cycle
from ..config import now_local
from ..database import get_db
from ..models import DISPUTED, OVERDUE, PAID, PAUSED, PENDING, Client, Invoice, Message, User
from ..schemas import (
    ImportResult,
    InvoiceCreate,
    InvoiceDetail,
    InvoiceOut,
    InvoiceUpdate,
    MessageOut,
    RemindResult,
    RunSummary,
)
from ..security import get_current_user
from ..services.delivery import deliver_message
from ..services.tones import next_reminder_date
from ..utils import normalize_phone
from .clients import get_owned_client

router = APIRouter(tags=["invoices"])


def _out(inv: Invoice) -> dict:
    data = InvoiceOut.model_validate(inv).model_dump()
    data["client_name"] = inv.client.name
    return data


def get_owned_invoice(db: Session, user: User, invoice_id: int) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if not inv or inv.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invoice not found")
    return inv


def _initial_status(due: date) -> str:
    return OVERDUE if due < now_local().date() else PENDING


@router.get("/invoices", response_model=list[InvoiceOut])
def list_invoices(status_filter: str | None = Query(default=None, alias="status"),
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    query = db.query(Invoice).filter(Invoice.user_id == user.id)
    if status_filter:
        query = query.filter(Invoice.status == status_filter)
    return [_out(i) for i in query.order_by(Invoice.due_date).all()]


@router.post("/invoices", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
def create_invoice(data: InvoiceCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    get_owned_client(db, user, data.client_id)
    if data.due_date < data.issue_date:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Due date cannot be before the issue date")
    inv = Invoice(user_id=user.id, status=_initial_status(data.due_date),
                  next_reminder_on=next_reminder_date(data.due_date, 0), **data.model_dump())
    db.add(inv)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "You already have an invoice with this number")
    return _out(inv)


@router.get("/invoices/{invoice_id}", response_model=InvoiceDetail)
def get_invoice(invoice_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    inv = get_owned_invoice(db, user, invoice_id)
    data = _out(inv)
    data["messages"] = [MessageOut.model_validate(m).model_dump() for m in inv.messages]
    return data


@router.patch("/invoices/{invoice_id}", response_model=InvoiceOut)
def update_invoice(invoice_id: int, data: InvoiceUpdate, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    inv = get_owned_invoice(db, user, invoice_id)
    changes = data.model_dump(exclude_unset=True)
    action = changes.pop("action", None)
    for field, value in changes.items():
        if value is not None:
            setattr(inv, field, value)

    today = now_local().date()
    if action == "mark_paid":
        inv.status, inv.paid_on, inv.next_reminder_on = PAID, today, None
        inv.needs_review, inv.note = False, ""
    elif action == "pause":
        inv.status = PAUSED
    elif action == "resume" and inv.status in (PAUSED, DISPUTED):
        inv.status = _initial_status(inv.due_date)
        inv.needs_review, inv.note = False, ""
        inv.next_reminder_on = next_reminder_date(inv.due_date, inv.reminder_count, today)
    elif action == "dispute":
        inv.status = DISPUTED
    elif action == "clear_review":
        inv.needs_review, inv.note = False, ""
    db.commit()
    return _out(inv)


@router.post("/invoices/{invoice_id}/remind", response_model=RemindResult)
def remind_now(invoice_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Send the next reminder now, ignoring the schedule (not the caps or quiet hours)."""
    inv = get_owned_invoice(db, user, invoice_id)
    state = run_invoice(db, inv.id, now_local(), force=True)
    return RemindResult(outcome=state.get("outcome", "unknown"), reason=state.get("reason", ""),
                        message_id=state.get("message_id"))


@router.post("/agent/run", response_model=RunSummary)
def run_agent_now(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Run today's follow-up cycle for this user's invoices (what the scheduler does automatically)."""
    return run_daily_cycle(db, user_id=user.id)


@router.post("/messages/{message_id}/approve", response_model=RemindResult)
def approve_message(message_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    message = db.get(Message, message_id)
    if not message or message.invoice.user_id != user.id or message.status != "pending_approval":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No draft waiting for approval")
    ok = deliver_message(db, message, now_local())
    return RemindResult(outcome="sent" if ok else "send_failed", message_id=message.id)


@router.post("/messages/{message_id}/reject", response_model=RemindResult)
def reject_message(message_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    message = db.get(Message, message_id)
    if not message or message.invoice.user_id != user.id or message.status != "pending_approval":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No draft waiting for approval")
    message.status = "rejected"
    db.commit()
    return RemindResult(outcome="rejected", message_id=message.id)


@router.get("/messages/pending", response_model=list[MessageOut])
def pending_messages(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return (
        db.query(Message).join(Invoice, Message.invoice_id == Invoice.id)
        .filter(Invoice.user_id == user.id, Message.status == "pending_approval").all()
    )


@router.post("/invoices/import", response_model=ImportResult)
async def import_csv(file: UploadFile = File(...), user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Bulk import. Columns: client_name, whatsapp_number, invoice_number, amount, issue_date,
    due_date (YYYY-MM-DD), payment_link (optional), description (optional)."""
    text = (await file.read()).decode("utf-8-sig")
    created, skipped, errors = 0, 0, []
    for line_no, row in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        try:
            phone = normalize_phone(row["whatsapp_number"])
            if not 11 <= len(phone) <= 15:
                raise ValueError("invalid WhatsApp number")
            client = (db.query(Client)
                      .filter(Client.user_id == user.id, Client.whatsapp_number == phone,
                              Client.name == row["client_name"].strip()).first())
            if client is None:
                client = Client(user_id=user.id, name=row["client_name"].strip(), whatsapp_number=phone)
                db.add(client)
                db.flush()
            number = row["invoice_number"].strip()
            if db.query(Invoice).filter(Invoice.user_id == user.id, Invoice.invoice_number == number).first():
                skipped += 1
                continue
            due = date.fromisoformat(row["due_date"].strip())
            db.add(Invoice(
                user_id=user.id, client_id=client.id, invoice_number=number,
                amount=float(row["amount"]), issue_date=date.fromisoformat(row["issue_date"].strip()),
                due_date=due, payment_link=(row.get("payment_link") or "").strip(),
                description=(row.get("description") or "").strip(),
                status=_initial_status(due), next_reminder_on=next_reminder_date(due, 0),
            ))
            db.commit()
            created += 1
        except (KeyError, ValueError) as exc:
            db.rollback()
            errors.append(f"line {line_no}: {exc}")
    return ImportResult(created=created, skipped=skipped, errors=errors)

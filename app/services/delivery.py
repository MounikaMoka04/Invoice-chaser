"""Sending a stored message and updating the invoice's reminder bookkeeping."""
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..models import Client, Invoice, Message, as_utc
from ..utils import format_inr
from . import whatsapp
from .tones import next_reminder_date, reminder_cap


def within_service_window(db: Session, client: Client, now: datetime) -> bool:
    """True if the client wrote to us in the last 24 hours (free-form text allowed)."""
    last_in = (
        db.query(Message)
        .filter(Message.client_id == client.id, Message.direction == "in")
        .order_by(Message.id.desc())
        .first()
    )
    return bool(last_in and 0 <= now.timestamp() - as_utc(last_in.created_at).timestamp() < 24 * 3600)


def _template_params(client: Client, invoice: Invoice) -> list[str]:
    return [client.name, invoice.invoice_number, format_inr(invoice.amount),
            invoice.due_date.strftime("%d %b %Y"), invoice.payment_link]


def deliver_message(db: Session, message: Message, now: datetime) -> bool:
    """Send an outbound message. For reminders, also advance the invoice's schedule."""
    invoice, client = message.invoice, message.client
    result = whatsapp.send_message(
        client.whatsapp_number,
        message.body,
        within_window=within_service_window(db, client, now),
        template_params=_template_params(client, invoice),
    )
    if not result.ok:
        message.status = "failed"
        db.commit()
        return False

    message.status = "sent"
    message.provider_message_id = result.provider_id
    message.created_at = now
    if message.kind == "reminder":
        today = now.date()
        invoice.reminder_count += 1
        invoice.first_reminder_on = invoice.first_reminder_on or today
        invoice.last_reminder_on = today
        invoice.next_reminder_on = next_reminder_date(invoice.due_date, invoice.reminder_count, today)
        if invoice.reminder_count >= reminder_cap():
            invoice.needs_review = True
            invoice.note = "Final automatic reminder sent. Please follow up with this client yourself."
    db.commit()
    return True


def send_text(db: Session, client: Client, invoice: Invoice, body: str, now: datetime, kind: str = "ack") -> Message:
    """Create and send a non-reminder message (e.g. an acknowledgement of a reply)."""
    message = Message(invoice_id=invoice.id, client_id=client.id, direction="out",
                      kind=kind, body=body, status="queued", created_at=now)
    db.add(message)
    db.flush()
    deliver_message(db, message, now)
    return message


def recent_reminder_count(db: Session, client: Client, now: datetime, days: int = 7) -> int:
    """How many reminders this client received in the last `days` days (across all invoices)."""
    since = now - timedelta(days=days)
    rows = (
        db.query(Message)
        .filter(Message.client_id == client.id, Message.direction == "out",
                Message.kind == "reminder", Message.status.in_(("sent", "delivered", "read")))
        .all()
    )
    return sum(1 for m in rows if as_utc(m.created_at) >= since)

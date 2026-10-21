from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import now_local
from ..database import get_db
from ..models import DISPUTED, OPEN_STATUSES, PAID, UNPAID_STATUSES, Invoice, Message, User, as_utc
from ..security import get_current_user

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _brief(inv: Invoice) -> dict:
    return {"id": inv.id, "invoice_number": inv.invoice_number, "client_name": inv.client.name,
            "amount": inv.amount, "due_date": inv.due_date, "status": inv.status,
            "next_reminder_on": inv.next_reminder_on, "note": inv.note}


@router.get("/summary")
def summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    now = now_local()
    today = now.date()
    invoices = db.query(Invoice).filter(Invoice.user_id == user.id).all()

    unpaid = [i for i in invoices if i.status in UNPAID_STATUSES]
    overdue = [i for i in unpaid if i.due_date < today]
    paid = [i for i in invoices if i.status == PAID]
    recovered = [i for i in paid if i.reminder_count >= 1]

    recovered_amount = sum(i.amount for i in recovered)
    overdue_amount = sum(i.amount for i in overdue)
    base = recovered_amount + overdue_amount

    days = [(i.paid_on - i.first_reminder_on).days for i in recovered if i.paid_on and i.first_reminder_on]

    reminders = (db.query(Message).join(Invoice, Message.invoice_id == Invoice.id)
                 .filter(Invoice.user_id == user.id, Message.direction == "out", Message.kind == "reminder").all())
    delivered_ok = sum(1 for m in reminders if m.status in ("sent", "delivered", "read"))
    failed = sum(1 for m in reminders if m.status == "failed")
    since = now - timedelta(days=30)
    sent_30d = sum(1 for m in reminders if m.status in ("sent", "delivered", "read") and as_utc(m.created_at) >= since)

    horizon = today + timedelta(days=7)
    upcoming = sorted(
        (i for i in invoices if i.status in OPEN_STATUSES and i.next_reminder_on and i.next_reminder_on <= horizon),
        key=lambda i: i.next_reminder_on)
    attention = [i for i in invoices if i.needs_review or i.status == DISPUTED]

    by_status: dict[str, int] = {}
    for i in invoices:
        by_status[i.status] = by_status.get(i.status, 0) + 1

    return {
        "outstanding_amount": round(sum(i.amount for i in unpaid), 2),
        "overdue_amount": round(overdue_amount, 2),
        "recovered_amount": round(recovered_amount, 2),
        "collected_amount": round(sum(i.amount for i in paid), 2),
        "recovery_rate": round(recovered_amount / base, 3) if base else None,
        "avg_days_to_payment_after_first_reminder": round(sum(days) / len(days), 1) if days else None,
        "reminders_sent_30d": sent_30d,
        "delivery_rate": round(delivered_ok / (delivered_ok + failed), 3) if (delivered_ok + failed) else None,
        "invoice_counts": by_status,
        "upcoming_reminders": [_brief(i) for i in upcoming],
        "needs_attention": [_brief(i) for i in attention],
    }

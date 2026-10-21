"""The daily job: refresh statuses, then run the reminder graph for every due invoice."""
import logging
from collections import Counter
from datetime import datetime

from sqlalchemy.orm import Session

from ..config import now_local
from ..models import OPEN_STATUSES, OVERDUE, PENDING, Invoice
from .graph import run_invoice

log = logging.getLogger("invoice_chaser.runner")


def refresh_overdue(db: Session, today) -> int:
    """pending -> overdue once the due date has passed."""
    changed = (
        db.query(Invoice)
        .filter(Invoice.status == PENDING, Invoice.due_date < today)
        .update({Invoice.status: OVERDUE}, synchronize_session=False)
    )
    db.commit()
    return changed


def run_daily_cycle(db: Session, now: datetime | None = None, user_id: int | None = None) -> dict:
    now = now or now_local()
    today = now.date()
    refresh_overdue(db, today)

    query = db.query(Invoice).filter(
        Invoice.status.in_(OPEN_STATUSES),
        Invoice.next_reminder_on.is_not(None),
        Invoice.next_reminder_on <= today,
    )
    if user_id is not None:
        query = query.filter(Invoice.user_id == user_id)
    invoice_ids = [inv.id for inv in query.all()]

    outcomes: Counter = Counter()
    for invoice_id in invoice_ids:
        try:
            state = run_invoice(db, invoice_id, now)
            outcomes[state.get("outcome", "unknown")] += 1
        except Exception:  # one bad invoice must never stop the whole run
            db.rollback()
            log.exception("Reminder run failed for invoice %s", invoice_id)
            outcomes["error"] += 1
    log.info("Daily run: checked=%s outcomes=%s", len(invoice_ids), dict(outcomes))
    return {"checked": len(invoice_ids), "outcomes": dict(outcomes)}

"""Process payloads from the WhatsApp webhook: delivery receipts and client replies."""
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from ..agent.graph import handle_reply
from ..models import OPEN_STATUSES, Client, Invoice, Message
from ..utils import normalize_phone

log = logging.getLogger("invoice_chaser.inbound")
_STATUS_RANK = {"sent": 1, "delivered": 2, "read": 3, "failed": 9}


def _find_target(db: Session, phone: str) -> tuple[Client, Invoice] | None:
    """Work out which client and invoice an incoming message is about."""
    candidates = db.query(Client).filter(Client.whatsapp_number == phone).all()
    best: tuple[int, Client, Message | None] | None = None
    for client in candidates:
        last_out = (db.query(Message)
                    .filter(Message.client_id == client.id, Message.direction == "out")
                    .order_by(Message.id.desc()).first())
        score = last_out.id if last_out else 0
        if best is None or score > best[0]:
            best = (score, client, last_out)
    if best is None:
        return None
    _, client, last_out = best
    if last_out is not None:
        return client, last_out.invoice
    open_invoice = (db.query(Invoice)
                    .filter(Invoice.client_id == client.id, Invoice.status.in_(OPEN_STATUSES))
                    .order_by(Invoice.due_date).first())
    return (client, open_invoice) if open_invoice else None


def process_payload(db: Session, payload: dict, now: datetime) -> dict:
    """Handle one Meta webhook payload. Returns counts, useful for tests and logs."""
    counts = {"replies": 0, "statuses": 0, "ignored": 0}
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            for st in value.get("statuses", []):
                message = db.query(Message).filter(Message.provider_message_id == st.get("id")).first()
                new = st.get("status", "")
                if message and _STATUS_RANK.get(new, 0) > _STATUS_RANK.get(message.status, 0):
                    message.status = new
                    counts["statuses"] += 1
            db.commit()

            for msg in value.get("messages", []):
                text = (msg.get("text") or {}).get("body", "").strip()
                if msg.get("type") != "text" or not text:
                    counts["ignored"] += 1
                    continue
                target = _find_target(db, normalize_phone(msg.get("from", "")))
                if target is None:
                    counts["ignored"] += 1
                    continue
                client, invoice = target
                inbound = Message(invoice_id=invoice.id, client_id=client.id, direction="in", kind="reply",
                                  body=text, status="received", created_at=now,
                                  provider_message_id=msg.get("id", ""))
                db.add(inbound)
                db.commit()
                state = handle_reply(db, invoice.id, text, now)
                inbound.intent = state.get("intent", "")
                db.commit()
                counts["replies"] += 1
    return counts

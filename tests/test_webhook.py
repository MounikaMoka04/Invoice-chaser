"""Tests for the WhatsApp webhook: verification, replies and delivery receipts."""
from datetime import timedelta

from app.agent.runner import run_daily_cycle
from app.models import Message
from app.services.inbound import process_payload

from .conftest import NOW


def _payload(phone: str, text: str | None = None, status: tuple[str, str] | None = None) -> dict:
    value: dict = {}
    if text is not None:
        value["messages"] = [{"from": phone, "id": "wamid.IN1", "type": "text", "text": {"body": text}}]
    if status:
        value["statuses"] = [{"id": status[0], "status": status[1]}]
    return {"entry": [{"changes": [{"value": value}]}]}


def _sent_invoice(db, make_invoice):
    inv = make_invoice(NOW.date())
    run_daily_cycle(db, NOW)
    db.refresh(inv)
    assert inv.reminder_count == 1
    return inv


def _acks(db, inv):
    return db.query(Message).filter(Message.invoice_id == inv.id, Message.kind == "ack").all()


def test_verification_handshake(api):
    ok = api.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.challenge": "1234",
                                               "hub.verify_token": "change-me-verify-token"})
    assert ok.status_code == 200 and ok.text == "1234"
    bad = api.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.challenge": "1",
                                                "hub.verify_token": "nope"})
    assert bad.status_code == 403


def test_client_says_paid_pauses_for_verification(db, make_invoice):
    inv = _sent_invoice(db, make_invoice)
    counts = process_payload(db, _payload(inv.client.whatsapp_number, "I have paid yesterday"), NOW)
    assert counts["replies"] == 1
    db.refresh(inv)
    assert inv.status == "paused" and inv.needs_review and "verify" in inv.note
    assert len(_acks(db, inv)) == 1
    inbound = db.query(Message).filter(Message.direction == "in").one()
    assert inbound.intent == "paid"


def test_more_time_reschedules(db, make_invoice):
    inv = _sent_invoice(db, make_invoice)
    process_payload(db, _payload(inv.client.whatsapp_number, "Need a few more days please"), NOW)
    db.refresh(inv)
    assert inv.next_reminder_on == NOW.date() + timedelta(days=3)
    assert inv.status == "pending"
    acks = _acks(db, inv)
    assert len(acks) == 1 and "check back" in acks[0].body


def test_dispute_stops_reminders_and_alerts_owner(db, make_invoice):
    inv = _sent_invoice(db, make_invoice)
    process_payload(db, _payload(inv.client.whatsapp_number, "This amount is wrong"), NOW)
    db.refresh(inv)
    assert inv.status == "disputed" and inv.needs_review


def test_stop_opts_client_out_everywhere(db, make_invoice):
    inv = _sent_invoice(db, make_invoice)
    process_payload(db, _payload(inv.client.whatsapp_number, "STOP"), NOW)
    db.refresh(inv)
    assert inv.client.opted_out and inv.status == "paused"


def test_unclear_reply_is_flagged_not_answered(db, make_invoice):
    inv = _sent_invoice(db, make_invoice)
    process_payload(db, _payload(inv.client.whatsapp_number, "ok"), NOW)
    db.refresh(inv)
    assert inv.needs_review and _acks(db, inv) == []


def test_unknown_number_is_ignored(db, make_invoice):
    _sent_invoice(db, make_invoice)
    counts = process_payload(db, _payload("919000000000", "hello"), NOW)
    assert counts == {"replies": 0, "statuses": 0, "ignored": 1}


def test_delivery_receipts_update_message_status(db, make_invoice):
    inv = _sent_invoice(db, make_invoice)
    out = db.query(Message).filter(Message.invoice_id == inv.id, Message.direction == "out").one()
    process_payload(db, _payload("", status=(out.provider_message_id, "delivered")), NOW)
    db.refresh(out)
    assert out.status == "delivered"
    process_payload(db, _payload("", status=(out.provider_message_id, "sent")), NOW)  # never goes backwards
    db.refresh(out)
    assert out.status == "delivered"


def test_webhook_endpoint_accepts_payload(api, db, make_invoice):
    inv = _sent_invoice(db, make_invoice)
    r = api.post("/webhooks/whatsapp", json=_payload(inv.client.whatsapp_number, "STOP"))
    assert r.status_code == 200 and r.json()["replies"] == 1

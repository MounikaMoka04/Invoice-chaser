"""Tests for the follow-up agent: schedule, tone ladder, guardrails."""
from datetime import datetime, timedelta

from app.agent.runner import run_daily_cycle
from app.models import Message
from app.services.tones import next_reminder_date, tone_for

from .conftest import IST, NOW


def _outbound(db, inv):
    return db.query(Message).filter(Message.invoice_id == inv.id, Message.direction == "out").all()


def test_schedule_and_tone_ladder():
    from datetime import date
    due = date(2026, 10, 1)
    assert [next_reminder_date(due, n) for n in range(6)] == [
        date(2026, 10, 1), date(2026, 10, 4), date(2026, 10, 8), date(2026, 10, 15), date(2026, 10, 22), None]
    assert [tone_for(n)["tone"] for n in range(5)] == ["friendly", "polite", "firm", "formal", "final"]
    assert tone_for(1, "friendly")["tone"] == "friendly"  # softened
    assert tone_for(0, "formal")["tone"] == "formal"  # always formal
    assert tone_for(4, "friendly")["tone"] == "final"  # never hide the final notice


def test_sends_first_reminder_on_due_date(db, make_invoice):
    inv = make_invoice(NOW.date())
    result = run_daily_cycle(db, NOW)
    assert result == {"checked": 1, "outcomes": {"sent": 1}}
    db.refresh(inv)
    assert inv.reminder_count == 1 and inv.first_reminder_on == NOW.date()
    msg = _outbound(db, inv)[0]
    assert msg.status == "sent" and msg.tone == "friendly" and msg.stage == 1
    assert "INV-001" in msg.body and "₹40,000" in msg.body and "https://pay.example.com/x" in msg.body


def test_does_not_send_before_due_date(db, make_invoice):
    inv = make_invoice(NOW.date() + timedelta(days=2))
    assert run_daily_cycle(db, NOW)["checked"] == 0
    assert _outbound(db, inv) == []


def test_tone_escalates_over_several_runs(db, make_invoice):
    inv = make_invoice(NOW.date() - timedelta(days=30))  # long overdue
    tones = []
    day = NOW
    for _ in range(6):
        run_daily_cycle(db, day)
        day += timedelta(days=8)
    db.refresh(inv)
    tones = [m.tone for m in _outbound(db, inv)]
    assert tones == ["friendly", "polite", "firm", "formal", "final"]
    assert inv.reminder_count == 5 and inv.next_reminder_on is None
    assert inv.needs_review  # owner is told to follow up personally
    assert inv.status == "overdue"


def test_late_invoice_is_not_chased_daily(db, make_invoice):
    inv = make_invoice(NOW.date() - timedelta(days=30))
    run_daily_cycle(db, NOW)
    run_daily_cycle(db, NOW + timedelta(days=1))  # next day: must not send again
    assert len(_outbound(db, inv)) == 1
    db.refresh(inv)
    assert inv.next_reminder_on >= NOW.date() + timedelta(days=2)


def test_stops_when_paid_paused_or_opted_out(db, make_invoice):
    paid = make_invoice(NOW.date())
    paid.status = "paid"
    paused = make_invoice(NOW.date())
    paused.status = "paused"
    opted = make_invoice(NOW.date())
    opted.client.opted_out = True
    db.commit()
    result = run_daily_cycle(db, NOW)
    assert result["outcomes"] == {"stopped": 1}  # only the opted-out one is still "open" to check
    for inv in (paid, paused, opted):
        assert _outbound(db, inv) == []


def test_quiet_hours_defer_then_send_next_run(db, make_invoice):
    inv = make_invoice(NOW.date())
    night = datetime(2026, 10, 21, 22, 30, tzinfo=IST)
    assert run_daily_cycle(db, night)["outcomes"] == {"deferred": 1}
    assert _outbound(db, inv) == []
    assert run_daily_cycle(db, NOW)["outcomes"] == {"sent": 1}


def test_weekly_message_cap_per_client(db, make_invoice):
    first = make_invoice(NOW.date())
    client = first.client
    # three more invoices for the same client, all due today
    from app.models import Invoice
    for n in range(3):
        db.add(Invoice(user_id=first.user_id, client_id=client.id, invoice_number=f"X-{n}", amount=500,
                       issue_date=NOW.date(), due_date=NOW.date(), status="pending",
                       next_reminder_on=NOW.date()))
    db.commit()
    result = run_daily_cycle(db, NOW)
    assert result["outcomes"] == {"sent": 3, "deferred": 1}  # limit is 3 reminders per client per week


def test_approval_mode_holds_draft_until_approved(db, make_invoice):
    inv = make_invoice(NOW.date())
    inv.user.require_approval = True
    db.commit()
    assert run_daily_cycle(db, NOW)["outcomes"] == {"awaiting_approval": 1}
    msg = _outbound(db, inv)[0]
    assert msg.status == "pending_approval"
    db.refresh(inv)
    assert inv.reminder_count == 0
    # a second run must not create another draft
    assert run_daily_cycle(db, NOW)["outcomes"] == {"stopped": 1}
    assert len(_outbound(db, inv)) == 1

    from app.services.delivery import deliver_message
    assert deliver_message(db, msg, NOW)
    db.refresh(inv)
    assert inv.reminder_count == 1 and msg.status == "sent"


def test_failed_send_is_retried_next_run(db, make_invoice, monkeypatch):
    from app.services import whatsapp
    inv = make_invoice(NOW.date())
    monkeypatch.setattr(whatsapp, "send_message",
                        lambda *a, **k: whatsapp.SendResult(ok=False, error="boom"))
    assert run_daily_cycle(db, NOW)["outcomes"] == {"send_failed": 1}
    db.refresh(inv)
    assert inv.reminder_count == 0 and inv.next_reminder_on == NOW.date()
    monkeypatch.undo()
    assert run_daily_cycle(db, NOW)["outcomes"] == {"sent": 1}


def test_pending_becomes_overdue(db, make_invoice):
    inv = make_invoice(NOW.date() - timedelta(days=1))
    run_daily_cycle(db, NOW)
    db.refresh(inv)
    assert inv.status == "overdue"

from datetime import timedelta

from app.agent.runner import run_daily_cycle
from app.security import create_access_token

from .conftest import NOW


def test_summary_numbers(api, db, make_invoice, monkeypatch):
    monkeypatch.setattr("app.routers.dashboard.now_local", lambda: NOW)  # pin "today"
    long_overdue = make_invoice(NOW.date() - timedelta(days=10), amount=10000)
    recovered = make_invoice(NOW.date() - timedelta(days=5), amount=40000)
    make_invoice(NOW.date() - timedelta(days=2), amount=2500)

    run_daily_cycle(db, NOW)  # every invoice gets its first reminder today

    # the client of `recovered` pays 3 days after that first reminder
    db.refresh(recovered)
    recovered.status, recovered.paid_on = "paid", NOW.date() + timedelta(days=3)
    db.commit()

    headers = {"Authorization": f"Bearer {create_access_token(long_overdue.user_id)}"}
    s = api.get("/dashboard/summary", headers=headers).json()
    assert s["recovered_amount"] == 40000
    assert s["overdue_amount"] == 12500  # 10,000 + 2,500 still unpaid and past due
    assert s["outstanding_amount"] == 12500
    assert s["recovery_rate"] == round(40000 / 52500, 3)
    assert s["avg_days_to_payment_after_first_reminder"] == 3.0
    assert s["delivery_rate"] == 1.0
    assert s["invoice_counts"] == {"overdue": 2, "paid": 1}
    assert len(s["upcoming_reminders"]) == 2  # the two unpaid invoices are due again within 7 days

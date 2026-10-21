from datetime import date, timedelta


def _client(api, headers, name="Asha Traders", number="98765 43210"):
    r = api.post("/clients", json={"name": name, "whatsapp_number": number}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _invoice(api, headers, client_id, number="INV-1", due=None, amount=15000):
    due = due or date.today() + timedelta(days=5)
    return api.post("/invoices", headers=headers, json={
        "client_id": client_id, "invoice_number": number, "amount": amount,
        "issue_date": str(due - timedelta(days=10)), "due_date": str(due),
        "payment_link": "https://pay.example.com/1"})


def test_register_login_me(api):
    r = api.post("/auth/register", json={"name": "A", "email": "A@Example.com", "password": "secret123"})
    assert r.status_code == 201 and r.json()["email"] == "a@example.com"
    assert api.post("/auth/register", json={"name": "A", "email": "a@example.com",
                                            "password": "secret123"}).status_code == 409
    bad = api.post("/auth/login", data={"username": "a@example.com", "password": "wrong"})
    assert bad.status_code == 401
    token = api.post("/auth/login", data={"username": "a@example.com", "password": "secret123"}).json()
    me = api.get("/auth/me", headers={"Authorization": f"Bearer {token['access_token']}"})
    assert me.json()["name"] == "A"


def test_endpoints_require_login(api):
    assert api.get("/invoices").status_code == 401
    assert api.get("/dashboard/summary").status_code == 401


def test_phone_is_normalised_and_validated(api, auth_headers):
    assert _client(api, auth_headers)["whatsapp_number"] == "919876543210"
    bad = api.post("/clients", json={"name": "X", "whatsapp_number": "123"}, headers=auth_headers)
    assert bad.status_code == 422


def test_invoice_lifecycle(api, auth_headers):
    c = _client(api, auth_headers)
    due = date.today() + timedelta(days=5)
    inv = _invoice(api, auth_headers, c["id"], due=due).json()
    assert inv["status"] == "pending"
    assert inv["next_reminder_on"] == str(due)  # first reminder goes out on the due date
    assert inv["client_name"] == "Asha Traders"

    assert _invoice(api, auth_headers, c["id"]).status_code == 409  # duplicate number

    paused = api.patch(f"/invoices/{inv['id']}", json={"action": "pause"}, headers=auth_headers).json()
    assert paused["status"] == "paused"
    resumed = api.patch(f"/invoices/{inv['id']}", json={"action": "resume"}, headers=auth_headers).json()
    assert resumed["status"] == "pending"
    paid = api.patch(f"/invoices/{inv['id']}", json={"action": "mark_paid"}, headers=auth_headers).json()
    assert paid["status"] == "paid" and paid["next_reminder_on"] is None and paid["paid_on"]

    assert len(api.get("/invoices?status=paid", headers=auth_headers).json()) == 1
    assert api.get("/invoices?status=pending", headers=auth_headers).json() == []


def test_past_due_invoice_is_overdue(api, auth_headers):
    c = _client(api, auth_headers)
    inv = _invoice(api, auth_headers, c["id"], due=date.today() - timedelta(days=4)).json()
    assert inv["status"] == "overdue"


def test_users_cannot_see_each_others_data(api, auth_headers):
    c = _client(api, auth_headers)
    inv = _invoice(api, auth_headers, c["id"]).json()
    api.post("/auth/register", json={"name": "B", "email": "b@example.com", "password": "secret123"})
    token = api.post("/auth/login", data={"username": "b@example.com", "password": "secret123"}).json()
    other = {"Authorization": f"Bearer {token['access_token']}"}
    assert api.get(f"/invoices/{inv['id']}", headers=other).status_code == 404
    assert api.get("/invoices", headers=other).json() == []


def test_remind_now_sends_and_records_message(api, auth_headers, monkeypatch):
    # Pin the clock to daytime so the quiet-hours rule does not make this test flaky.
    from datetime import datetime
    from zoneinfo import ZoneInfo
    monkeypatch.setattr("app.routers.invoices.now_local",
                        lambda: datetime.now(ZoneInfo("Asia/Kolkata")).replace(hour=12))
    c = _client(api, auth_headers)
    inv = _invoice(api, auth_headers, c["id"], due=date.today() + timedelta(days=2)).json()
    r = api.post(f"/invoices/{inv['id']}/remind", headers=auth_headers).json()
    assert r["outcome"] == "sent", r
    detail = api.get(f"/invoices/{inv['id']}", headers=auth_headers).json()
    assert detail["reminder_count"] == 1
    assert detail["messages"][0]["direction"] == "out" and "INV-1" in detail["messages"][0]["body"]


def test_csv_import(api, auth_headers):
    csv_text = (
        "client_name,whatsapp_number,invoice_number,amount,issue_date,due_date,payment_link,description\n"
        "Asha Traders,9876543210,A-1,1000,2026-01-01,2026-01-15,,Logo\n"
        "Asha Traders,9876543210,A-1,1000,2026-01-01,2026-01-15,,duplicate\n"
        "Bad Row,123,A-2,1000,2026-01-01,2026-01-15,,\n"
    )
    r = api.post("/invoices/import", headers=auth_headers,
                 files={"file": ("invoices.csv", csv_text, "text/csv")}).json()
    assert r["created"] == 1 and r["skipped"] == 1 and len(r["errors"]) == 1
    assert len(api.get("/clients", headers=auth_headers).json()) == 1


def test_deleting_client_removes_their_data(api, auth_headers):
    c = _client(api, auth_headers)
    _invoice(api, auth_headers, c["id"])
    assert api.delete(f"/clients/{c['id']}", headers=auth_headers).status_code == 204
    assert api.get("/invoices", headers=auth_headers).json() == []


def test_dashboard_summary_empty(api, auth_headers):
    s = api.get("/dashboard/summary", headers=auth_headers).json()
    assert s["outstanding_amount"] == 0 and s["recovery_rate"] is None

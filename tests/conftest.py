import os
import tempfile
from datetime import date, datetime
from zoneinfo import ZoneInfo

# Configure the app for testing BEFORE it is imported.
_tmp = tempfile.mkdtemp()
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_tmp}/test.db",
    "SCHEDULER_ENABLED": "false",
    "LLM_PROVIDER": "template",
    "WHATSAPP_MODE": "mock",
    "WHATSAPP_APP_SECRET": "",
    "REQUIRE_APPROVAL": "false",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Client, Invoice, User  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.services.tones import next_reminder_date  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 21, 11, 0, tzinfo=IST)  # a Wednesday, 11:00 IST (outside quiet hours)


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def api():
    return TestClient(app)


@pytest.fixture
def auth_headers(api):
    api.post("/auth/register", json={"name": "Riya", "email": "riya@example.com",
                                     "password": "secret123", "business_name": "Riya Designs"})
    token = api.post("/auth/login", data={"username": "riya@example.com", "password": "secret123"}).json()
    return {"Authorization": f"Bearer {token['access_token']}"}


@pytest.fixture
def make_invoice(db):
    """Factory: create a user, a client and an invoice directly in the database."""
    counter = {"n": 0}

    def _make(due: date, amount: float = 40000, tone: str = "neutral", **extra) -> Invoice:
        counter["n"] += 1
        n = counter["n"]
        user = db.query(User).first() or User(name="Riya", email="r@example.com",
                                              password_hash=hash_password("secret123"),
                                              business_name="Riya Designs")
        db.add(user)
        db.flush()
        client = db.query(Client).filter(Client.name == f"Client {n}").first() or Client(
            user_id=user.id, name=f"Client {n}", whatsapp_number=f"9198765432{n:02d}", preferred_tone=tone)
        db.add(client)
        db.flush()
        inv = Invoice(user_id=user.id, client_id=client.id, invoice_number=f"INV-{n:03d}", amount=amount,
                      issue_date=due, due_date=due, payment_link="https://pay.example.com/x",
                      status="pending", next_reminder_on=next_reminder_date(due, 0), **extra)
        db.add(inv)
        db.commit()
        return inv

    return _make

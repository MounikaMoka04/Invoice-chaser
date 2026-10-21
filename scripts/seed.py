"""Create a demo account with realistic invoices so you can try the app straight away.

    python -m scripts.seed

Log in with  demo@example.com / demo1234
"""
from datetime import timedelta

from app.config import now_local
from app.database import Base, SessionLocal, engine
from app.models import Client, Invoice, User
from app.security import hash_password
from app.services.tones import next_reminder_date

DEMO_EMAIL = "demo@example.com"


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    if db.query(User).filter(User.email == DEMO_EMAIL).first():
        print("Demo data already exists. Log in with demo@example.com / demo1234")
        return

    user = User(name="Riya Sharma", email=DEMO_EMAIL, password_hash=hash_password("demo1234"),
                business_name="Riya Designs")
    db.add(user)
    db.flush()

    clients = {
        "Asha Traders": Client(user_id=user.id, name="Asha Traders", whatsapp_number="919876543210", preferred_tone="friendly"),
        "Kiran Foods": Client(user_id=user.id, name="Kiran Foods", whatsapp_number="919812345678", preferred_tone="neutral"),
        "Nova Labs": Client(user_id=user.id, name="Nova Labs", whatsapp_number="919900112233", preferred_tone="formal"),
    }
    db.add_all(clients.values())
    db.flush()

    today = now_local().date()
    # (client, number, amount, days until due (negative = already overdue), description)
    rows = [
        ("Asha Traders", "INV-101", 40000, -12, "Logo and brand kit"),
        ("Kiran Foods", "INV-102", 18500, -4, "Menu design"),
        ("Nova Labs", "INV-103", 72000, 0, "Website redesign, phase 1"),
        ("Asha Traders", "INV-104", 9500, 6, "Social media pack"),
        ("Kiran Foods", "INV-105", 25000, 15, "Packaging illustrations"),
    ]
    for client_name, number, amount, offset, description in rows:
        due = today + timedelta(days=offset)
        db.add(Invoice(
            user_id=user.id, client_id=clients[client_name].id, invoice_number=number, amount=amount,
            description=description, issue_date=due - timedelta(days=14), due_date=due,
            payment_link=f"https://pay.example.com/{number.lower()}",
            status="overdue" if due < today else "pending", next_reminder_on=next_reminder_date(due, 0),
        ))
    db.commit()
    print("Created demo data. Log in with demo@example.com / demo1234")


if __name__ == "__main__":
    main()

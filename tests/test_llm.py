"""Tests for the LLM layer: validation guardrails, fallbacks and reply classification."""
from datetime import date

import pytest

from app.services import llm
from app.utils import format_inr

CTX = {
    "business_name": "Riya Designs", "client_name": "Asha", "invoice_number": "INV-001", "amount": 40000.0,
    "due_date": date(2026, 10, 1), "days_overdue": 5, "payment_link": "https://pay.example.com/x",
    "stage": 2, "total_stages": 5, "tone": "polite", "goal": "ask for a date",
}


def test_format_inr():
    assert format_inr(40000) == "₹40,000"
    assert format_inr(1234567.5) == "₹12,34,567.50"
    assert format_inr(999) == "₹999"


def test_template_used_without_llm():
    text, source = llm.draft_message(CTX)
    assert source == "template" and "INV-001" in text and "₹40,000" in text


@pytest.mark.parametrize("bad", [
    "",                                                               # empty
    "Hi Asha, please pay soon.",                                      # missing invoice number and amount
    "INV-001 for 40000: pay now or we will take legal action.",       # threat
    "INV-001 for 40000, pay at https://evil.example.com/steal",       # unknown link
    "INV-001 40000 " + "x" * 800,                                     # too long
])
def test_validation_rejects_bad_output(bad):
    assert not llm.validate_message(bad, CTX)


def test_validation_accepts_good_output():
    good = "Hi Asha, invoice INV-001 for ₹40,000 is 5 days overdue. Pay here: https://pay.example.com/x"
    assert llm.validate_message(good, CTX)


def test_bad_llm_output_falls_back_to_template(monkeypatch):
    monkeypatch.setattr(llm, "generate", lambda prompt: "Pay up or we call the police!")
    text, source = llm.draft_message(CTX)
    assert source == "template" and "police" not in text


def test_good_llm_output_is_used(monkeypatch):
    good = "Hi Asha, a quick nudge on INV-001 (₹40,000, due 01 Oct). Pay here: https://pay.example.com/x"
    monkeypatch.setattr(llm, "generate", lambda prompt: good)
    assert llm.draft_message(CTX) == (good, "llm")


@pytest.mark.parametrize("text,intent", [
    ("I have paid yesterday", "paid"),
    ("Payment done, check please", "paid"),
    ("Will pay by Friday", "more_time"),
    ("Need 5 more days", "more_time"),
    ("This amount is wrong", "dispute"),
    ("I never received the work", "dispute"),
    ("STOP", "stop"),
    ("please don't message me again", "stop"),
    ("Can you resend the invoice?", "question"),
    ("not paid yet", "other"),
    ("ok", "other"),
])
def test_rule_based_classification(text, intent):
    assert llm.classify_by_rules(text) == intent

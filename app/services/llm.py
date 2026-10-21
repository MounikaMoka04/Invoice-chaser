"""LLM helpers: write reminder text and classify client replies.

The LLM has two narrow jobs. Dates, amounts and the decision to send stay in Python.
Every model output is validated, and a fixed template is the fallback, so the system
still works with no API key, when a rate limit is hit, or when the model misbehaves.
"""
import logging
import re
from datetime import date

import httpx

from ..config import settings
from ..utils import format_inr

log = logging.getLogger("invoice_chaser.llm")

INTENTS = ("paid", "more_time", "dispute", "stop", "question", "other")
_FORBIDDEN = ("legal action", "lawyer", "court", "police", "sue ", "arrest", "blacklist")


# --------------------------------------------------------------------------
# Provider calls
# --------------------------------------------------------------------------
def _call_gemini(prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent"
    resp = httpx.post(
        url,
        headers={"x-goog-api-key": settings.gemini_api_key},
        json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.4}},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


def _call_groq(prompt: str) -> str:
    resp = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.groq_api_key}"},
        json={
            "model": settings.groq_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.4,
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def llm_available() -> bool:
    return (settings.llm_provider == "gemini" and bool(settings.gemini_api_key)) or (
        settings.llm_provider == "groq" and bool(settings.groq_api_key)
    )


def generate(prompt: str) -> str | None:
    """Call the configured provider. Returns None when unavailable or on any error."""
    if not llm_available():
        return None
    try:
        return _call_gemini(prompt) if settings.llm_provider == "gemini" else _call_groq(prompt)
    except Exception as exc:  # network error, rate limit, bad payload ...
        log.warning("LLM call failed, using fallback: %s", exc)
        return None


# --------------------------------------------------------------------------
# Reminder drafting
# --------------------------------------------------------------------------
def _pay_line(ctx: dict) -> str:
    return f" You can pay here: {ctx['payment_link']}" if ctx.get("payment_link") else ""


def template_message(ctx: dict) -> str:
    """Fixed wording per stage. Used as the fallback and when no LLM is configured."""
    name, biz = ctx["client_name"], ctx["business_name"] or "us"
    no, amt, due = ctx["invoice_number"], format_inr(ctx["amount"]), ctx["due_date"].strftime("%d %b %Y")
    pay = _pay_line(ctx)
    overdue = ctx["days_overdue"]
    stage = ctx["stage"]
    if stage <= 1:
        return (f"Hi {name}, a friendly reminder from {biz}: invoice {no} for {amt} "
                f"is due on {due}.{pay} Thank you!")
    if stage == 2:
        return (f"Hi {name}, this is a gentle follow-up from {biz}. Invoice {no} for {amt} "
                f"(due {due}) is still open.{pay} Could you let us know when we can expect payment?")
    if stage == 3:
        return (f"Hello {name}, invoice {no} for {amt} is now {overdue} days overdue (due {due}).{pay} "
                f"Please confirm a payment date today so we can keep our records in order.")
    if stage == 4:
        return (f"Dear {name}, invoice {no} for {amt} from {biz} is {overdue} days past its due date "
                f"of {due}.{pay} If there is any issue with the invoice, please tell us so we can resolve it.")
    return (f"Dear {name}, this is our final courtesy notice from {biz} for invoice {no} ({amt}, "
            f"due {due}).{pay} Without a response, the account will be reviewed by the business owner.")


def _prompt(ctx: dict, pref: str) -> str:
    return (
        "You write short WhatsApp payment reminders for a freelancer or small agency in India.\n"
        "Write ONE message in plain text, 2 to 4 sentences, no emojis, no markdown.\n"
        "Rules: be polite and specific; mention the invoice number, the amount and the due date; "
        "include the payment link if one is given; never threaten legal action; never invent facts.\n\n"
        f"Sender business: {ctx['business_name'] or 'the freelancer'}\n"
        f"Client name: {ctx['client_name']}\n"
        f"Invoice number: {ctx['invoice_number']}\n"
        f"Amount: {format_inr(ctx['amount'])}\n"
        f"Due date: {ctx['due_date'].strftime('%d %b %Y')}\n"
        f"Days overdue: {ctx['days_overdue']}\n"
        f"Payment link: {ctx.get('payment_link') or 'none'}\n"
        f"Reminder number: {ctx['stage']} of {ctx['total_stages']}\n"
        f"Tone to use: {ctx['tone']}\n"
        f"Goal of this message: {ctx['goal']}\n"
        f"Client's preferred style: {pref}\n"
    )


def validate_message(text: str, ctx: dict) -> bool:
    """Guardrail: reject output that is empty, too long, off-topic or threatening."""
    if not text or len(text) > 700:
        return False
    lowered = text.lower()
    if any(word in lowered for word in _FORBIDDEN):
        return False
    if ctx["invoice_number"].lower() not in lowered:
        return False
    amount_digits = str(int(round(ctx["amount"])))
    if amount_digits not in re.sub(r"[,\s]", "", text):
        return False
    link = ctx.get("payment_link")
    urls = re.findall(r"https?://\S+", text)
    return all(link and u.rstrip(".,)") == link for u in urls)


def draft_message(ctx: dict, preferred_tone: str = "neutral") -> tuple[str, str]:
    """Return (text, source) where source is 'llm' or 'template'."""
    raw = generate(_prompt(ctx, preferred_tone))
    if raw:
        text = raw.strip().strip('"')
        if validate_message(text, ctx):
            return text, "llm"
        log.warning("LLM draft rejected by validation; using template")
    return template_message(ctx), "template"


# --------------------------------------------------------------------------
# Reply classification
# --------------------------------------------------------------------------
_STOP = re.compile(r"\b(stop|unsubscribe|do not (message|contact|text)|don'?t (message|contact|text))\b", re.I)
_NEG_PAID = re.compile(r"\b(not|n't|never|yet to|haven'?t)\b.{0,20}\bpaid\b", re.I)
_DISPUTE = re.compile(
    r"\b(wrong|incorrect|dispute|mistake|error|overcharg\w*|not agreed|do not owe|don'?t owe|"
    r"never (received|ordered|got)|didn'?t (receive|order|get)|not (delivered|my|our))\b", re.I)
_PAID = re.compile(r"\b(paid|payment (done|made|sent|completed)|transferred|done)\b|sent the (money|payment)", re.I)
_TIME = re.compile(
    r"(more time|extension|few days|more (days|weeks)|next (week|month)|tomorrow|will pay|pay (on|by)|"
    r"\bby (mon|tues|wednes|thurs|fri|satur|sun)day|\b(till|until)\b|\d+ days)", re.I)


def classify_by_rules(text: str) -> str:
    if _STOP.search(text):
        return "stop"
    if _NEG_PAID.search(text):
        return "other"
    if _DISPUTE.search(text):
        return "dispute"
    if _PAID.search(text):
        return "paid"
    if _TIME.search(text):
        return "more_time"
    if "?" in text:
        return "question"
    return "other"


def classify_reply(text: str) -> str:
    """Classify a client reply. Rules decide clear cases; the LLM helps with the rest."""
    intent = classify_by_rules(text)
    if intent in ("stop", "paid", "dispute", "more_time") or not llm_available():
        return intent
    raw = generate(
        "Classify this WhatsApp reply from a client about an unpaid invoice. "
        f"Answer with exactly one word from: {', '.join(INTENTS)}.\n\nReply: {text}"
    )
    if raw:
        word = raw.strip().lower().strip(".")
        if word in INTENTS:
            return word
    return intent

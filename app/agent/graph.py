"""The follow-up agent, built with LangGraph.

Two small graphs:

1. reminder graph (outbound)
       load -> check_limits -> draft -> approval_gate -> send -> END
   Every node can end the run early (invoice paid, quiet hours, cap reached ...).

2. reply graph (inbound)
       classify -> (paid | more_time | dispute | stop | other) -> END

The decision to message a client is made by plain Python rules in these nodes.
The LLM is only called inside `draft` (wording) and `classify` (reading a reply).
The database session travels in `config["configurable"]["db"]`.
"""
from datetime import datetime, timedelta
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from ..config import settings
from ..models import DISPUTED, OPEN_STATUSES, PAID, PAUSED, Invoice, Message
from ..services import llm
from ..services.delivery import deliver_message, recent_reminder_count, send_text
from ..services.tones import LADDER, reminder_cap, tone_for

# ---------------------------------------------------------------------------
# Reminder graph
# ---------------------------------------------------------------------------


class ReminderState(TypedDict, total=False):
    invoice_id: int
    now: datetime
    force: bool
    outcome: str  # sent | stopped | not_due | deferred | cap_reached | awaiting_approval | send_failed | missing
    reason: str
    message_id: int | None
    ctx: dict
    body: str
    tone: str
    stage: int
    source: str


def in_quiet_hours(now: datetime) -> bool:
    start, end = settings.quiet_start_hour, settings.quiet_end_hour
    return now.hour >= start or now.hour < end


def _db(config):
    return config["configurable"]["db"]


def load(state: ReminderState, config) -> dict:
    db = _db(config)
    inv = db.get(Invoice, state["invoice_id"])
    if inv is None:
        return {"outcome": "missing", "reason": "invoice not found"}
    if inv.status in (PAID, PAUSED, DISPUTED):
        return {"outcome": "stopped", "reason": f"invoice is {inv.status}"}
    if inv.client.opted_out:
        return {"outcome": "stopped", "reason": "client opted out"}
    if inv.needs_review:
        return {"outcome": "stopped", "reason": "invoice needs your review first"}
    waiting = db.query(Message).filter(
        Message.invoice_id == inv.id, Message.status == "pending_approval"
    ).first()
    if waiting:
        return {"outcome": "stopped", "reason": "a draft is waiting for your approval"}
    return {}


def check_limits(state: ReminderState, config) -> dict:
    db = _db(config)
    inv = db.get(Invoice, state["invoice_id"])
    now, today = state["now"], state["now"].date()
    if inv.reminder_count >= reminder_cap():
        inv.needs_review = True
        inv.next_reminder_on = None
        inv.note = inv.note or "Reminder limit reached. Please follow up yourself."
        db.commit()
        return {"outcome": "cap_reached", "reason": "reminder limit reached"}
    if not state.get("force") and (inv.next_reminder_on is None or inv.next_reminder_on > today):
        return {"outcome": "not_due", "reason": "next reminder is not due yet"}
    if in_quiet_hours(now):
        return {"outcome": "deferred", "reason": "quiet hours, will retry in the next run"}
    if recent_reminder_count(db, inv.client, now) >= settings.max_messages_per_client_per_week:
        return {"outcome": "deferred", "reason": "weekly message limit for this client reached"}
    return {}


def draft(state: ReminderState, config) -> dict:
    db = _db(config)
    inv = db.get(Invoice, state["invoice_id"])
    client, user = inv.client, inv.user
    step = tone_for(inv.reminder_count, client.preferred_tone)
    ctx = {
        "business_name": user.business_name or user.name,
        "client_name": client.name,
        "invoice_number": inv.invoice_number,
        "amount": inv.amount,
        "due_date": inv.due_date,
        "days_overdue": max(0, (state["now"].date() - inv.due_date).days),
        "payment_link": inv.payment_link,
        "stage": step["stage"],
        "total_stages": len(LADDER),
        "tone": step["tone"],
        "goal": step["goal"],
    }
    body, source = llm.draft_message(ctx, client.preferred_tone)
    return {"ctx": ctx, "body": body, "tone": step["tone"], "stage": step["stage"], "source": source}


def approval_gate(state: ReminderState, config) -> dict:
    """In approval mode, store the draft for the owner instead of sending it."""
    db = _db(config)
    inv = db.get(Invoice, state["invoice_id"])
    pending = Message(
        invoice_id=inv.id, client_id=inv.client_id, direction="out", kind="reminder",
        body=state["body"], tone=state["tone"], stage=state["stage"], created_at=state["now"],
        status="pending_approval" if (inv.user.require_approval or settings.require_approval) else "queued",
    )
    db.add(pending)
    db.commit()
    if pending.status == "pending_approval":
        return {"outcome": "awaiting_approval", "reason": "waiting for your approval", "message_id": pending.id}
    return {"message_id": pending.id}


def send(state: ReminderState, config) -> dict:
    db = _db(config)
    message = db.get(Message, state["message_id"])
    ok = deliver_message(db, message, state["now"])
    if ok:
        return {"outcome": "sent", "reason": f"{state['tone']} reminder {state['stage']} sent"}
    return {"outcome": "send_failed", "reason": "WhatsApp send failed, will retry in the next run"}


def _continue_or_end(state: ReminderState) -> str:
    return "end" if state.get("outcome") else "next"


def build_reminder_graph():
    g = StateGraph(ReminderState)
    g.add_node("load", load)
    g.add_node("check_limits", check_limits)
    g.add_node("draft", draft)
    g.add_node("approval_gate", approval_gate)
    g.add_node("send", send)
    g.add_edge(START, "load")
    g.add_conditional_edges("load", _continue_or_end, {"next": "check_limits", "end": END})
    g.add_conditional_edges("check_limits", _continue_or_end, {"next": "draft", "end": END})
    g.add_edge("draft", "approval_gate")
    g.add_conditional_edges("approval_gate", _continue_or_end, {"next": "send", "end": END})
    g.add_edge("send", END)
    return g.compile()


reminder_graph = build_reminder_graph()


def run_invoice(db, invoice_id: int, now: datetime, force: bool = False) -> dict:
    """Run the reminder graph for one invoice and return the final state."""
    return reminder_graph.invoke(
        {"invoice_id": invoice_id, "now": now, "force": force},
        config={"configurable": {"db": db}},
    )


# ---------------------------------------------------------------------------
# Reply graph
# ---------------------------------------------------------------------------
ACK_PAID = "Thank you! We'll confirm the payment and update our records."
ACK_MORE_TIME = "No problem, thanks for letting us know. We'll check back with you on {date}."
ACK_DISPUTE = "Thanks for flagging this. {business} will review it and get back to you shortly."
ACK_STOP = "Understood. You won't receive any more automated reminders from us."


class ReplyState(TypedDict, total=False):
    invoice_id: int
    text: str
    now: datetime
    intent: str
    action: str


def classify(state: ReplyState, config) -> dict:
    return {"intent": llm.classify_reply(state["text"])}


def _route(state: ReplyState) -> str:
    return state["intent"] if state["intent"] in ("paid", "more_time", "dispute", "stop") else "other"


def _ack(config, state, body):
    db = _db(config)
    inv = db.get(Invoice, state["invoice_id"])
    send_text(db, inv.client, inv, body, state["now"])


def act_paid(state: ReplyState, config) -> dict:
    db = _db(config)
    inv = db.get(Invoice, state["invoice_id"])
    # The client says they paid. We never trust that blindly: pause and ask the owner to verify.
    inv.status = PAUSED
    inv.needs_review = True
    inv.note = "Client says they have paid. Please verify and mark the invoice as paid."
    db.commit()
    _ack(config, state, ACK_PAID)
    return {"action": "paused_for_verification"}


def act_more_time(state: ReplyState, config) -> dict:
    db = _db(config)
    inv = db.get(Invoice, state["invoice_id"])
    new_date = state["now"].date() + timedelta(days=3)
    inv.next_reminder_on = new_date
    inv.note = f"Client asked for more time. Next reminder moved to {new_date:%d %b}."
    db.commit()
    _ack(config, state, ACK_MORE_TIME.format(date=new_date.strftime("%d %b")))
    return {"action": "rescheduled"}


def act_dispute(state: ReplyState, config) -> dict:
    db = _db(config)
    inv = db.get(Invoice, state["invoice_id"])
    inv.status = DISPUTED
    inv.needs_review = True
    inv.note = "Client disputes this invoice. Please contact them directly."
    db.commit()
    _ack(config, state, ACK_DISPUTE.format(business=inv.user.business_name or inv.user.name))
    return {"action": "disputed"}


def act_stop(state: ReplyState, config) -> dict:
    db = _db(config)
    inv = db.get(Invoice, state["invoice_id"])
    client = inv.client
    client.opted_out = True
    for other in client.invoices:
        if other.status in OPEN_STATUSES:
            other.status = PAUSED
            other.note = "Client asked to stop messages."
    db.commit()
    _ack(config, state, ACK_STOP)
    return {"action": "opted_out"}


def act_other(state: ReplyState, config) -> dict:
    db = _db(config)
    inv = db.get(Invoice, state["invoice_id"])
    inv.needs_review = True
    inv.note = "Client sent a message that needs your reply."
    db.commit()
    return {"action": "flagged_for_review"}


def build_reply_graph():
    g = StateGraph(ReplyState)
    g.add_node("classify", classify)
    for name, fn in (("paid", act_paid), ("more_time", act_more_time), ("dispute", act_dispute),
                     ("stop", act_stop), ("other", act_other)):
        g.add_node(name, fn)
        g.add_edge(name, END)
    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", _route, ["paid", "more_time", "dispute", "stop", "other"])
    return g.compile()


reply_graph = build_reply_graph()


def handle_reply(db, invoice_id: int, text: str, now: datetime) -> dict:
    """Record the intent on the inbound message, then act on it."""
    state = reply_graph.invoke(
        {"invoice_id": invoice_id, "text": text, "now": now},
        config={"configurable": {"db": db}},
    )
    return state

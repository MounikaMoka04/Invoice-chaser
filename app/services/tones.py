"""The reminder schedule and the escalating tone ladder.

Everything here is plain Python, not AI: *when* to send and *how firm* to be are
business rules that must be predictable and testable. The LLM only writes the words.
"""
from datetime import date, timedelta

from ..config import settings

# Days after the due date on which reminder 1, 2, 3 ... are scheduled.
SCHEDULE_DAYS = [0, 3, 7, 14, 21]

LADDER = [
    {"stage": 1, "tone": "friendly", "goal": "A gentle heads-up that the invoice is due, with the payment link."},
    {"stage": 2, "tone": "polite", "goal": "Politely ask the client to confirm when they will pay."},
    {"stage": 3, "tone": "firm", "goal": "Courteously but clearly state the overdue amount and ask for a firm payment date."},
    {"stage": 4, "tone": "formal", "goal": "Formally note the invoice is well overdue and offer help resolving any issue."},
    {"stage": 5, "tone": "final", "goal": "A final courtesy notice; say the account will be reviewed by the business owner."},
]
_TONE_ORDER = [step["tone"] for step in LADDER]


def reminder_cap() -> int:
    """Maximum number of automatic reminders per invoice."""
    return max(1, min(settings.max_reminders, len(SCHEDULE_DAYS)))


def next_reminder_date(due: date, reminders_sent: int, today: date | None = None) -> date | None:
    """Date of the next reminder, or None when the cap is reached.

    If the scheduled date is already in the past (an invoice entered late), the next
    reminder is pushed out by MIN_GAP_DAYS so clients are never messaged daily.
    """
    if reminders_sent >= reminder_cap():
        return None
    scheduled = due + timedelta(days=SCHEDULE_DAYS[reminders_sent])
    if today is not None and reminders_sent > 0:
        scheduled = max(scheduled, today + timedelta(days=settings.min_gap_days))
    return scheduled


def tone_for(reminders_sent: int, preferred: str = "neutral") -> dict:
    """Pick the ladder step for the next reminder, adjusted by the client's preference."""
    idx = min(reminders_sent, len(LADDER) - 1)
    last = len(LADDER) - 1
    if preferred == "friendly" and 0 < idx < last:
        idx -= 1  # soften one level, but never hide the final notice
    elif preferred == "formal" and idx < 3:
        idx = 3  # always at least formal
    step = dict(LADDER[idx])
    step["stage"] = reminders_sent + 1
    return step

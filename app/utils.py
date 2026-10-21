"""Small helpers shared across the app."""
import re


def normalize_phone(raw: str) -> str:
    """Keep digits only. A bare 10-digit number is assumed to be Indian (+91)."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        digits = "91" + digits
    return digits


def format_inr(amount: float) -> str:
    """Format a rupee amount with Indian digit grouping, e.g. 1234567.5 -> ₹12,34,567.50."""
    whole, dec = f"{abs(amount):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    text = whole if dec == "00" else f"{whole}.{dec}"
    return f"₹{text}"

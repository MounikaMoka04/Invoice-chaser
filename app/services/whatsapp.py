"""WhatsApp delivery.

* mock  - logs the message and pretends it was sent (default; safe for development).
* cloud - Meta's WhatsApp Cloud API.

WhatsApp rule: a business may only send free-form text inside 24 hours after the
customer last wrote. Outside that window it must send a pre-approved *template*.
"""
import logging
import uuid
from dataclasses import dataclass

import httpx

from ..config import settings

log = logging.getLogger("invoice_chaser.whatsapp")
GRAPH_URL = "https://graph.facebook.com/v20.0"


@dataclass
class SendResult:
    ok: bool
    provider_id: str = ""
    error: str = ""


def send_message(to: str, body: str, *, within_window: bool, template_params: list[str]) -> SendResult:
    """Send `body` as free text inside the 24h window, otherwise send the approved template.

    template_params is the ordered list of values for the template placeholders:
    [client_name, invoice_number, amount, due_date, payment_link].
    """
    if settings.whatsapp_mode != "cloud":
        log.info("[MOCK WHATSAPP] to=%s window=%s\n%s", to, within_window, body)
        return SendResult(ok=True, provider_id=f"mock-{uuid.uuid4().hex[:12]}")

    if not (settings.whatsapp_token and settings.whatsapp_phone_number_id):
        return SendResult(ok=False, error="WhatsApp cloud credentials are not configured")

    if within_window:
        payload = {"messaging_product": "whatsapp", "to": to, "type": "text",
                   "text": {"body": body, "preview_url": True}}
    else:
        payload = {
            "messaging_product": "whatsapp", "to": to, "type": "template",
            "template": {
                "name": settings.whatsapp_template_name,
                "language": {"code": settings.whatsapp_template_lang},
                "components": [{
                    "type": "body",
                    "parameters": [{"type": "text", "text": p or "-"} for p in template_params],
                }],
            },
        }
    try:
        resp = httpx.post(
            f"{GRAPH_URL}/{settings.whatsapp_phone_number_id}/messages",
            headers={"Authorization": f"Bearer {settings.whatsapp_token}"},
            json=payload, timeout=20,
        )
        resp.raise_for_status()
        return SendResult(ok=True, provider_id=resp.json()["messages"][0]["id"])
    except Exception as exc:
        log.warning("WhatsApp send failed: %s", exc)
        return SendResult(ok=False, error=str(exc))

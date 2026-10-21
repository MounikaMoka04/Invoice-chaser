import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from ..config import now_local, settings
from ..database import get_db
from ..services.inbound import process_payload

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get("/whatsapp", response_class=PlainTextResponse)
def verify_webhook(mode: str = Query("", alias="hub.mode"), token: str = Query("", alias="hub.verify_token"),
                   challenge: str = Query("", alias="hub.challenge")):
    """Meta calls this once when you register the webhook URL."""
    if mode == "subscribe" and hmac.compare_digest(token, settings.whatsapp_verify_token):
        return challenge
    raise HTTPException(403, "Verification failed")


@router.post("/whatsapp")
async def receive_webhook(request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    if settings.whatsapp_app_secret:
        expected = "sha256=" + hmac.new(settings.whatsapp_app_secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, request.headers.get("X-Hub-Signature-256", "")):
            raise HTTPException(403, "Invalid signature")
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "Invalid JSON")
    return {"status": "ok", **process_payload(db, payload, now_local())}

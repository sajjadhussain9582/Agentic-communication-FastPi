from __future__ import annotations
from typing import Any
from sqlmodel import Session
from app.models.form import Form
from app.models.form_submission import FormSubmission
from app.services.intake_service import process_inbound_message

def _extract_user_message(body: dict[str, Any]) -> str:
    for key in ("message", "body", "text", "inquiry", "details"):
        v = body.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:16000]
    skip = {"channel", "Channel"}
    parts = [f"{k}: {v}" for k, v in body.items() if k not in skip and v]
    return "\n".join(parts)[:16000] or "(empty submission)"

import asyncio

async def process_form_submission(session: Session, form_id: int, body: dict[str, Any]) -> dict[str, Any]:
    form = session.get(Form, form_id)
    if not form:
        return {"error": "form_not_found"}

    channel = str(body.get("channel") or body.get("Channel") or "website")[:100]
    user_text = _extract_user_message(body)
    
    sender_details = {
        "email": body.get("email"),
        "phone": body.get("phone"),
        "name": body.get("name") or body.get("username")
    }

    # This will now handle contact/conversation creation and trigger the AI pipeline
    await process_inbound_message(session, channel, sender_details, user_text)

    # The rest of the function can be simplified as the core logic is now in intake_service
    # For now, we will just return a success message
    return {"status": "success", "message": "Form submission processed."}

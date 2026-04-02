from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from app.core.config import settings
from app.db.session import get_session
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.integration import Integration, IntegrationRun, WebhookEvent
from app.models.message import Message
from app.services.ai_graph import run_ai_pipeline

router = APIRouter(prefix="/webhooks/ghl", tags=["Webhooks (GHL)"])


def _verify_ghl_webhook(request: Request) -> None:
    expected = (settings.GHL_WEBHOOK_SECRET or "").strip()
    if not expected:
        return
    h = request.headers.get("X-GHL-Secret") or ""
    auth = (request.headers.get("Authorization") or "").replace("Bearer ", "").strip()
    if h == expected or auth == expected:
        return
    raise HTTPException(status_code=401, detail="Invalid webhook secret")


def _upsert_contact_from_payload(session: Session, body: dict[str, Any]) -> Contact:
    email = body.get("email") or body.get("contact_email")
    phone = body.get("phone") or body.get("contact_phone")
    name = body.get("name") or body.get("contact_name") or "GHL Contact"
    company = body.get("company")

    row = None
    if email:
        row = session.exec(select(Contact).where(Contact.email == str(email))).first()
    if not row and phone:
        row = session.exec(select(Contact).where(Contact.phone == str(phone))).first()
    if not row:
        row = Contact(
            email=str(email) if email else None,
            phone=str(phone) if phone else None,
            username=str(name)[:255],
            company=str(company)[:255] if company else None,
            source="webhook:ghl",
            status="new",
        )
    ext = row.external_ids if isinstance(row.external_ids, dict) else {}
    contact_id = body.get("contactId") or body.get("contact_id")
    if contact_id:
        ext["ghl_contact_id"] = str(contact_id)
    row.external_ids = ext
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _ensure_open_conversation(session: Session, contact: Contact, channel: str) -> Conversation:
    conv = session.exec(
        select(Conversation)
        .where(Conversation.contact_id == contact.id)
        .where(Conversation.channel == channel)
        .order_by(Conversation.id.desc())
    ).first()
    if conv and conv.status in ("open", "pending_human"):
        return conv
    conv = Conversation(contact_id=contact.id, channel=channel, status="open")
    session.add(conv)
    session.commit()
    session.refresh(conv)
    return conv


@router.post("/message")
async def webhook_ghl_message(
    request: Request,
    session: Session = Depends(get_session),
):
    _verify_ghl_webhook(request)
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        body = {}

    ev = WebhookEvent(provider="ghl", event_type="message", payload=body, processed=False)
    session.add(ev)
    ghl = session.exec(select(Integration).where(Integration.provider == "ghl")).first()
    if ghl:
        session.add(
            IntegrationRun(
                integration_id=ghl.id,
                action="webhook_message",
                status="received",
            )
        )
    contact = _upsert_contact_from_payload(session, body)
    channel = str(body.get("channel") or "email")[:100]
    conv = _ensure_open_conversation(session, contact, channel)
    text = str(body.get("message") or body.get("text") or "").strip()[:32000]
    if text:
        inbound = Message(
            conversation_id=conv.id,
            conversation_public_uuid=conv.public_uuid,
            sender_type="client",
            message_type="text",
            message=text,
            channel=channel,
            is_generated=False,
            is_handled=False,
        )
        session.add(inbound)
        session.commit()
        session.refresh(inbound)
        if settings.OPENAI_API_KEY:
            try:
                await run_ai_pipeline(session, conv, contact, inbound, body)
            except Exception:
                pass
    session.commit()
    session.refresh(ev)
    return {"ok": True, "event_id": ev.id}


@router.post("/contact")
async def webhook_ghl_contact(
    request: Request,
    session: Session = Depends(get_session),
):
    _verify_ghl_webhook(request)
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        body = {}

    ev = WebhookEvent(provider="ghl", event_type="contact", payload=body, processed=False)
    session.add(ev)
    ghl = session.exec(select(Integration).where(Integration.provider == "ghl")).first()
    if ghl:
        session.add(
            IntegrationRun(
                integration_id=ghl.id,
                action="webhook_contact",
                status="received",
            )
        )
    _upsert_contact_from_payload(session, body)
    session.commit()
    session.refresh(ev)
    return {"ok": True, "event_id": ev.id}

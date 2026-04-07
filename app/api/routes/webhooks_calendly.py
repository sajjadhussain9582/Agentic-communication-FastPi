from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select
from app.db.session import get_session
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.integration import Integration, WebhookEvent
from datetime import datetime

router = APIRouter(prefix="/webhooks/calendly", tags=["Webhooks"])

@router.post("")
async def calendly_webhook(
    request: Request,
    session: Session = Depends(get_session),
):
    payload = await request.json()
    event_type = payload.get("event")
    
    # Store raw event
    webhook_event = WebhookEvent(
        provider="calendly",
        event_type=event_type or "unknown",
        payload=payload,
    )
    session.add(webhook_event)
    session.commit()
    
    if event_type == "invitee.created":
        await handle_invitee_created(payload, session)
    elif event_type == "invitee.canceled":
        await handle_invitee_canceled(payload, session)
    
    webhook_event.processed = True
    session.add(webhook_event)
    session.commit()
    
    return {"ok": True}

async def handle_invitee_created(payload: Dict[str, Any], session: Session):
    data = payload.get("payload", {})
    raw_email = data.get("email")
    name = data.get("name")
    
    if not raw_email:
        return
    
    email = raw_email.strip().lower()
    
    # Find or create contact
    contact = session.exec(select(Contact).where(Contact.email == email)).first()
    if not contact:
        contact = Contact(
            email=email,
            username=name or email.split("@")[0],
            status="meeting_booked",
            pipeline_stage="meeting_booked"
        )
    else:
        contact.pipeline_stage = "meeting_booked"
        contact.status = "meeting_booked"
    
    session.add(contact)
    session.commit()
    session.refresh(contact)
    
    # Find or create conversation
    conversation = session.exec(
        select(Conversation)
        .where(Conversation.contact_id == contact.id)
        .where(Conversation.channel == "scheduling")
    ).first()
    
    if not conversation:
        conversation = Conversation(
            contact_id=contact.id,
            channel="scheduling",
            status="open"
        )
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
    
    # Add message about the booking
    event_name = data.get("event_type", {}).get("name", "Meeting")
    start_time = data.get("start_time")
    msg_text = f"Scheduled {event_name} for {start_time}"
    
    message = Message(
        conversation_id=conversation.id,
        conversation_public_uuid=conversation.public_uuid,
        sender_type="client",
        message=msg_text,
        channel="scheduling",
        is_handled=True
    )
    session.add(message)
    
    conversation.updated_at = datetime.utcnow()
    session.add(conversation)
    session.commit()

async def handle_invitee_canceled(payload: Dict[str, Any], session: Session):
    # Similar logic for cancellation
    pass

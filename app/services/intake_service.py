
from __future__ import annotations
from typing import Any
from sqlmodel import Session, select, or_, desc
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.message import Message
from app.services.ai_graph import run_ai_pipeline
from app.services.channel_delivery import deliver_message
from app.core.config import settings
from datetime import datetime
import asyncio
import logging
import os

logger = logging.getLogger(__name__)

def _default_placeholder_reply() -> str:
    return (
        "Thanks for reaching out. We connect you with vetted contractors, real estate agents, developers, architects, and home builders. "
        "To match you with the right partner, can you share your scope, budget range, timeline, and location (and who the decision maker is)? "
        "If you're ready, we can schedule a quick call or consultation to confirm next steps."
    )

async def process_inbound_message(session: Session, channel: str, sender_details: dict[str, Any], message_body: str, subject: str = None) -> Conversation:
    logger.info(f"Processing inbound {channel} message from {sender_details} subject: {subject}")
    raw_email = sender_details.get("email")
    phone = sender_details.get("phone")
    name = sender_details.get("name")
    
    email = raw_email.strip().lower() if raw_email else None

    contact = None
    if email or phone:
        filters = []
        if email:
            filters.append(Contact.email == email)
        if phone:
            filters.append(Contact.phone == phone)
        if filters:
            contact = session.exec(select(Contact).where(or_(*filters))).first()

    if not contact:
        contact = Contact(
            email=email,
            phone=phone,
            username=name,
            source=channel,
            status="new",
        )
    else:
        if name and not contact.username:
            contact.username = name
        contact.updated_at = datetime.utcnow()

    session.add(contact)
    session.commit()
    session.refresh(contact)

    conv = session.exec(
        select(Conversation)
        .where(Conversation.contact_id == contact.id)
        .where(Conversation.channel == channel)
        .where(Conversation.status == "open")
        .order_by(desc(Conversation.id))
    ).first()

    if not conv:
        conv = Conversation(
            contact_id=contact.id,
            channel=channel,
            status="open",
        )
        session.add(conv)
        session.commit()
        session.refresh(conv)

    user_text = f"Subject: {subject}\n\n{message_body}" if subject else message_body

    inbound = Message(
        conversation_id=conv.id,
        conversation_public_uuid=conv.public_uuid,
        sender_type="client",
        message_type="text",
        message=user_text,
        channel=channel,
        is_generated=False,
        is_handled=False,
    )
    session.add(inbound)
    session.commit()
    session.refresh(inbound)

    outbound = None
    ai_enabled = bool(getattr(settings, "GROQ_API_KEY", "") and getattr(settings, "GROQ_CHAT_MODEL", ""))
    if os.getenv("PYTEST_CURRENT_TEST") and os.getenv("RUN_AI_TESTS", "").strip().lower() not in ("1", "true", "yes", "on"):
        ai_enabled = False

    if ai_enabled:
        try:
            logger.info(f"Running AI pipeline for conversation {conv.id}, contact {contact.id}")
            outbound = await run_ai_pipeline(session, conv, contact, inbound, {})
            if outbound:
                logger.info(f"Generated outbound message: {outbound.message[:100]}...")
                await deliver_message(
                    session,
                    channel=outbound.channel,
                    recipient=contact.email if outbound.channel == "email" else contact.phone,
                    message=outbound.message,
                    metadata={"subject": f"Re: {subject}" if subject else "Re: Project Inquiry"},
                )
        except Exception as e:
            logger.error(f"Error running AI pipeline: {e}")
            outbound = None

    if outbound is None:
        placeholder = Message(
            conversation_id=conv.id,
            conversation_public_uuid=conv.public_uuid,
            sender_type="agent",
            sender_id=None,
            message_type="text",
            message=_default_placeholder_reply(),
            channel=channel,
            is_generated=True,
            is_handled=True,
            rag_source_kb_ids=[],
        )
        inbound.is_handled = True
        session.add(inbound)
        session.add(placeholder)
        session.commit()
    
    return conv


from __future__ import annotations
from typing import Any
from sqlmodel import Session, select, or_, desc
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.message import Message
from app.services.ai_graph import run_ai_pipeline
from app.services.channel_delivery import deliver_message
from datetime import datetime
import asyncio
import logging
import re

logger = logging.getLogger(__name__)


_SIGNATURE_SEPARATOR_RE = re.compile(r"(?im)^\s*(?:--\s*$|best regards,?\s*$|regards,?\s*$|thanks,?\s*$|sent from my .*$)")
_QUOTED_REPLY_MARKERS = (
    "on ",
    "wrote:",
    "from:",
    "sent:",
    "to:",
    "subject:",
)


def _strip_quoted_thread(message_body: str) -> str:
    """Keep only the newest human-written segment from an email thread."""
    text = (message_body or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    kept: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            kept.append("")
            continue
        if stripped.startswith(">"):
            break
        if stripped.lower().startswith(_QUOTED_REPLY_MARKERS):
            if stripped.startswith("On ") and " wrote:" not in stripped.lower():
                kept.append(line)
                continue
            break
        if _SIGNATURE_SEPARATOR_RE.match(stripped):
            break
        kept.append(line)

    cleaned = "\n".join(kept).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned

async def process_inbound_message(session: Session, channel: str, sender_details: dict[str, Any], message_body: str, subject: str = None) -> Conversation:
    logger.info(f"Processing inbound {channel} message from {sender_details} subject: {subject}")
    email = sender_details.get("email")
    phone = sender_details.get("phone")
    name = sender_details.get("name")
    email = sender_details.get("email")
    phone = sender_details.get("phone")
    name = sender_details.get("name")

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

    cleaned_body = _strip_quoted_thread(message_body)
    stored_message = cleaned_body or (subject or "").strip() or (message_body or "").strip()

    inbound = Message(
        conversation_id=conv.id,
        conversation_public_uuid=conv.public_uuid,
        sender_type="client",
        message_type="text",
        message=stored_message,
        channel=channel,
        is_generated=False,
        is_handled=False,
    )
    session.add(inbound)
    session.commit()
    session.refresh(inbound)

    try:
        logger.info(f"Running AI pipeline for conversation {conv.id}, contact {contact.id}")
        outbound = await run_ai_pipeline(
            session,
            conv,
            contact,
            inbound,
            {"subject": subject or "", "raw_body": message_body, "cleaned_body": cleaned_body},
            inbound_subject=subject or "",
        )
        if outbound:
            logger.info(f"Generated outbound message: {outbound.message[:100]}...")
            # Fix: call with keyword arguments as deliver_message expects them
            await deliver_message(
                session,
                channel=outbound.channel,
                recipient=contact.email if outbound.channel == "email" else contact.phone,
                message=outbound.message,
                metadata={"subject": f"Re: {subject}" if subject else "Re: Project Inquiry"}
            )
    except Exception as e:
        logger.error(f"Error running AI pipeline: {e}")
    
    return conv

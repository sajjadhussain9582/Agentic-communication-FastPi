
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

async def process_inbound_message(session: Session, channel: str, sender_details: dict[str, Any], message_body: str, subject: str = None):
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

    try:
        outbound = run_ai_pipeline(session, conv, contact, inbound, {})
        if outbound:
            # Fix: call with keyword arguments as deliver_message expects them
            await deliver_message(
                session,
                channel=outbound.channel,
                recipient=contact.email if outbound.channel == "email" else contact.phone,
                message=outbound.message,
                metadata={"subject": f"Re: {subject}" if subject else "Re: Project Inquiry"}
            )
    except Exception as e:
        print(f"Error running AI pipeline: {e}")

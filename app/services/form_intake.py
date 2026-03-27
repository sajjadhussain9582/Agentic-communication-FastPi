"""Form submission → contact, submission, conversation, inbound message → AI reply."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session, select, or_, desc

from app.core.config import settings
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.form import Form
from app.models.form_submission import FormSubmission
from app.models.message import Message
from app.services.ai_graph import run_ai_pipeline


def _extract_user_message(body: dict[str, Any]) -> str:
    for key in ("message", "body", "text", "inquiry", "details"):
        v = body.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:16000]
    # fallback: human-readable subset
    skip = {"channel", "Channel"}
    parts = [f"{k}: {v}" for k, v in body.items() if k not in skip and v]
    return "\n".join(parts)[:16000] or "(empty submission)"


def process_form_submission(session: Session, form_id: int, body: dict[str, Any]) -> dict[str, Any]:
    form = session.get(Form, form_id)
    if not form:
        return {"error": "form_not_found"}

    channel = str(body.get("channel") or body.get("Channel") or "website")[:100]
    user_text = _extract_user_message(body)

    email = body.get("email")
    phone = body.get("phone")
    username = body.get("name") or body.get("username")
    company = body.get("company")

    # 1. Match or Create Contact
    contact = None
    if email or phone:
        filters = []
        if email:
            filters.append(Contact.email == str(email))
        if phone:
            filters.append(Contact.phone == str(phone))
        if filters:
            contact = session.exec(select(Contact).where(or_(*filters))).first()

    if not contact:
        contact = Contact(
            email=str(email) if email else None,
            phone=str(phone) if phone else None,
            username=str(username)[:255] if username else None,
            company=str(company)[:255] if company else None,
            source=f"form:{form_id}",
            status="new",
        )
    else:
        # Update existing contact with fresh info
        if username:
            contact.username = str(username)[:255]
        if company:
            contact.company = str(company)[:255]
        contact.updated_at = datetime.utcnow()

    session.add(contact)
    session.commit()
    session.refresh(contact)

    submission = FormSubmission(
        form_id=form_id,
        contact_id=contact.id,
        json_response=body,
    )
    session.add(submission)
    session.commit()
    session.refresh(submission)

    # 2. Match or Create Conversation
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

    if settings.OPENAI_API_KEY:
        try:
            outbound = run_ai_pipeline(session, conv, contact, inbound, body)
        except Exception as e:
            session.refresh(inbound)
            session.refresh(conv)
            session.refresh(contact)
            ob = Message(
                conversation_id=conv.id,
                conversation_public_uuid=conv.public_uuid,
                sender_type="agent",
                message_type="text",
                message=f"We received your message. (AI processing error: {e!s})",
                channel=channel,
                is_generated=True,
                is_handled=False,
                rag_source_kb_ids=[],
            )
            session.add(ob)
            session.commit()
            session.refresh(ob)
            outbound = ob
    else:
        ob = Message(
            conversation_id=conv.id,
            conversation_public_uuid=conv.public_uuid,
            sender_type="agent",
            message_type="text",
            message=(
                "Thank you for reaching out about your project. "
                "We support growth partnerships by connecting clients with vetted contractors, real estate agents, developers, architects, and home builders. "
                "To tailor the next step, please share your project scope, budget range, timeline, and location/market. "
                "If useful, include who will join as the decision-maker. "
                "We can schedule a short consultation call and match you with the most relevant partner profile."
            ),
            channel=channel,
            is_generated=True,
            is_handled=True,
            rag_source_kb_ids=[],
        )
        inbound.is_handled = True
        session.add(inbound)
        session.add(ob)
        session.commit()
        session.refresh(ob)
        outbound = ob

    return {
        "submission_id": submission.id,
        "submission_uuid": submission.public_uuid,
        "contact_id": contact.id,
        "contact_uuid": contact.public_uuid,
        "conversation_id": conv.id,
        "conversation_uuid": conv.public_uuid,
        "inbound_message_id": inbound.id,
        "inbound_message_uuid": inbound.public_uuid,
        "ai_reply": outbound.message,
        "ai_reply_message_id": outbound.id,
        "ai_reply_message_uuid": outbound.public_uuid,
        "rag_kb_ids": outbound.rag_source_kb_ids or [],
        "conversation_status": session.get(Conversation, conv.id).status,
        "is_escalated": session.get(Conversation, conv.id).is_escalated,
    }

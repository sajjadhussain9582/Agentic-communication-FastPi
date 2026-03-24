import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, desc, or_, select

from app.api.deps.auth import get_current_user
from app.core.config import settings
from app.db.session import get_session
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.forms_agent import ConversationDetailRead, MessageRead
from app.services.ai_graph import run_ai_pipeline
from app.services.channel_delivery import deliver_message
from app.services.workflow_engine import schedule_reminder_workflow

router = APIRouter(prefix="/conversations", tags=["Conversations"])

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)


def _resolve_conversation(session: Session, ref: str) -> Optional[Conversation]:
    if UUID_RE.match(ref.strip()):
        return session.exec(
            select(Conversation).where(Conversation.public_uuid == ref.strip())
        ).first()
    try:
        cid = int(ref)
    except ValueError:
        return None
    return session.get(Conversation, cid)


class InboxItemRead(BaseModel):
    uuid: str
    contact_preview: dict[str, Any]
    last_message_preview: Optional[str] = None
    updated_at: Any
    is_escalated: bool
    channel: str
    status: str


class PostMessageBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=32000)
    sender_type: str = Field(default="human")
    channel: Optional[str] = None
    recipient_email: Optional[str] = None
    recipient_phone: Optional[str] = None
    deliver: bool = True


class ReminderBody(BaseModel):
    reminder_at: str = Field(..., min_length=1, max_length=200)
    note: Optional[str] = None


class InboundBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=32000)
    channel: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


def _optional_submit_auth(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
):
    if settings.FORM_SUBMIT_API_KEY and x_api_key != settings.FORM_SUBMIT_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


class InboundReplyRead(BaseModel):
    conversation_uuid: str
    inbound_message_uuid: str
    ai_reply_message_uuid: str
    ai_reply: str
    conversation_status: str
    is_escalated: bool


@router.get("", response_model=list[InboxItemRead])
def list_conversations(
    cursor: Optional[str] = Query(None, description="Opaque cursor (conversation id)"),
    limit: int = Query(50, ge=1, le=100),
    channel: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    is_escalated: Optional[bool] = Query(None),
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    q = select(Conversation)
    if channel:
        q = q.where(Conversation.channel == channel)
    if status:
        q = q.where(Conversation.status == status)
    if is_escalated is not None:
        q = q.where(Conversation.is_escalated == is_escalated)
    q = q.order_by(desc(Conversation.updated_at), desc(Conversation.id))
    if cursor:
        try:
            cid = int(cursor)
            c0 = session.get(Conversation, cid)
            if c0:
                q = q.where(
                    or_(
                        Conversation.updated_at < c0.updated_at,
                        (Conversation.updated_at == c0.updated_at)
                        & (Conversation.id < c0.id),
                    )
                )
        except ValueError:
            pass
    rows = list(session.exec(q.limit(limit)).all())
    out: list[InboxItemRead] = []
    for c in rows:
        contact = session.get(Contact, c.contact_id) if c.contact_id else None
        preview = {}
        if contact:
            preview = {
                "uuid": contact.public_uuid,
                "email": contact.email,
                "username": contact.username,
                "company": contact.company,
            }
        last_msg = session.exec(
            select(Message)
            .where(Message.conversation_id == c.id)
            .order_by(desc(Message.created_at), desc(Message.id))
            .limit(1)
        ).first()
        last_preview = (last_msg.message[:200] + "…") if last_msg and len(last_msg.message) > 200 else (last_msg.message if last_msg else None)
        out.append(
            InboxItemRead(
                uuid=c.public_uuid,
                contact_preview=preview,
                last_message_preview=last_preview,
                updated_at=c.updated_at,
                is_escalated=c.is_escalated,
                channel=c.channel,
                status=c.status,
            )
        )
    return out


@router.post("/{conversation_uuid}/messages", response_model=MessageRead)
def post_conversation_message(
    conversation_uuid: str,
    body: PostMessageBody,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not UUID_RE.match(conversation_uuid.strip()):
        raise HTTPException(400, "conversation_uuid must be a UUID")
    c = session.exec(
        select(Conversation).where(Conversation.public_uuid == conversation_uuid.strip())
    ).first()
    if not c:
        raise HTTPException(404, "Conversation not found")
    ch = (body.channel or c.channel or "website")[:100]
    st = body.sender_type if body.sender_type in ("human", "agent", "client") else "human"
    m = Message(
        conversation_id=c.id,
        conversation_public_uuid=c.public_uuid,
        sender_type=st,
        message_type="text",
        message=body.message.strip()[:32000],
        channel=ch,
        is_generated=False,
        is_handled=True,
    )
    if body.deliver and st in ("human", "agent"):
        recipient = body.recipient_email or body.recipient_phone
        if not recipient and c.contact_id:
            contact = session.get(Contact, c.contact_id)
            if contact:
                recipient = contact.email if ch == "email" else (contact.phone or contact.email)
        deliver_message(
            session,
            channel=ch,
            recipient=recipient,
            message=m.message,
            metadata={"conversation_uuid": c.public_uuid, "sender_type": st},
        )
    session.add(m)
    c.updated_at = m.created_at
    session.add(c)
    session.commit()
    session.refresh(m)
    return MessageRead(
        id=m.id,
        uuid=m.public_uuid,
        conversation_uuid=m.conversation_public_uuid,
        sender_type=m.sender_type,
        message=m.message,
        channel=m.channel,
        is_generated=m.is_generated,
        rag_source_kb_ids=m.rag_source_kb_ids,
        created_at=m.created_at,
    )


@router.post("/{conversation_uuid}/inbound", response_model=InboundReplyRead)
def post_conversation_inbound(
    conversation_uuid: str,
    body: InboundBody,
    session: Session = Depends(get_session),
    _auth: None = Depends(_optional_submit_auth),
):
    if not UUID_RE.match(conversation_uuid.strip()):
        raise HTTPException(400, "conversation_uuid must be a UUID")
    c = session.exec(
        select(Conversation).where(Conversation.public_uuid == conversation_uuid.strip())
    ).first()
    if not c:
        raise HTTPException(404, "Conversation not found")
    contact = session.get(Contact, c.contact_id) if c.contact_id else None
    if not contact:
        raise HTTPException(404, "Contact not found for conversation")
    ch = (body.channel or c.channel or "website")[:100]
    inbound = Message(
        conversation_id=c.id,
        conversation_public_uuid=c.public_uuid,
        sender_type="client",
        message_type="text",
        message=body.message.strip()[:32000],
        channel=ch,
        is_generated=False,
        is_handled=False,
    )
    session.add(inbound)
    session.commit()
    session.refresh(inbound)

    outbound = run_ai_pipeline(
        session=session,
        conversation=c,
        contact=contact,
        inbound_message=inbound,
        json_response=body.metadata or {"source": "conversation_inbound", "channel": ch},
    )
    latest_conv = session.get(Conversation, c.id)
    return InboundReplyRead(
        conversation_uuid=c.public_uuid,
        inbound_message_uuid=inbound.public_uuid,
        ai_reply_message_uuid=outbound.public_uuid,
        ai_reply=outbound.message,
        conversation_status=latest_conv.status if latest_conv else c.status,
        is_escalated=bool(latest_conv.is_escalated) if latest_conv else bool(c.is_escalated),
    )


@router.post("/{conversation_uuid}/schedule-reminder")
def schedule_conversation_reminder(
    conversation_uuid: str,
    body: ReminderBody,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not UUID_RE.match(conversation_uuid.strip()):
        raise HTTPException(400, "conversation_uuid must be a UUID")
    c = session.exec(
        select(Conversation).where(Conversation.public_uuid == conversation_uuid.strip())
    ).first()
    if not c:
        raise HTTPException(404, "Conversation not found")
    schedule_reminder_workflow(
        session,
        conversation_uuid=conversation_uuid.strip(),
        reminder_at=body.reminder_at,
        note=body.note,
    )
    return {"ok": True, "conversation_uuid": conversation_uuid.strip(), "reminder_at": body.reminder_at}


@router.get("/{conversation_ref}", response_model=ConversationDetailRead)
def get_conversation(
    conversation_ref: str,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    c = _resolve_conversation(session, conversation_ref)
    if not c:
        raise HTTPException(404, "Conversation not found")
    contact = session.get(Contact, c.contact_id) if c.contact_id else None
    msgs = list(
        session.exec(select(Message).where(Message.conversation_id == c.id)).all()
    )
    msgs.sort(key=lambda m: (m.created_at.timestamp() if m.created_at else 0, m.id or 0))
    return ConversationDetailRead(
        id=c.id,
        uuid=c.public_uuid,
        contact_id=c.contact_id,
        contact_uuid=contact.public_uuid if contact else None,
        channel=c.channel,
        status=c.status,
        is_escalated=c.is_escalated,
        last_intent=c.last_intent,
        qualification_stage=c.qualification_stage,
        messages=[
            MessageRead(
                id=m.id,
                uuid=m.public_uuid,
                conversation_uuid=m.conversation_public_uuid,
                sender_type=m.sender_type,
                message=m.message,
                channel=m.channel,
                is_generated=m.is_generated,
                rag_source_kb_ids=m.rag_source_kb_ids,
                created_at=m.created_at,
            )
            for m in msgs
        ],
    )

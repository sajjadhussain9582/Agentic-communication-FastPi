from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app.api.deps.auth import get_current_user
from app.db.session import get_session
from app.models.conversation import Conversation
from app.models.integration import IntegrationRun
from app.models.message import Message
from app.models.user import User

router = APIRouter(prefix="/ops", tags=["Ops"])


class OpsSummaryRead(BaseModel):
    generated_messages_24h: int
    escalated_conversations_24h: int
    reminders_scheduled_24h: int
    outbound_deliveries_24h: int


@router.get("/quality-summary", response_model=OpsSummaryRead)
def quality_summary(
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    since = datetime.utcnow() - timedelta(hours=24)
    generated = session.exec(
        select(func.count(Message.id)).where(Message.created_at >= since).where(Message.is_generated == True)  # noqa: E712
    ).one()
    escalated = session.exec(
        select(func.count(Conversation.id))
        .where(Conversation.updated_at >= since)
        .where(Conversation.is_escalated == True)  # noqa: E712
    ).one()
    reminders = session.exec(
        select(func.count(IntegrationRun.id))
        .where(IntegrationRun.created_at >= since)
        .where(IntegrationRun.action == "schedule_reminder")
    ).one()
    outbound = session.exec(
        select(func.count(IntegrationRun.id))
        .where(IntegrationRun.created_at >= since)
        .where(IntegrationRun.action.like("outbound_%"))
    ).one()
    return OpsSummaryRead(
        generated_messages_24h=int(generated or 0),
        escalated_conversations_24h=int(escalated or 0),
        reminders_scheduled_24h=int(reminders or 0),
        outbound_deliveries_24h=int(outbound or 0),
    )

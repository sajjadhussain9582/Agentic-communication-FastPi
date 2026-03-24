"""Simple trigger-action workflow engine with auditability."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.integration import Integration, IntegrationRun


def _workflow_integration(session: Session) -> Integration:
    row = session.exec(select(Integration).where(Integration.provider == "workflow")).first()
    if row:
        return row
    row = Integration(provider="workflow", status="connected")
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def run_decision_workflows(
    session: Session,
    *,
    conversation: Conversation,
    contact: Contact,
    decision: dict[str, Any],
) -> None:
    integration = _workflow_integration(session)
    action = "decision_eval"
    detail: dict[str, Any] = {
        "conversation_uuid": conversation.public_uuid,
        "contact_uuid": contact.public_uuid,
        "intent": decision.get("last_intent"),
        "persona_segment": decision.get("persona_segment"),
        "qualification_stage": decision.get("qualification_stage"),
    }

    if bool(decision.get("is_escalated")):
        conversation.status = "pending_human"
        action = "notify_human"
    elif str(decision.get("qualification_stage") or "") == "qualified":
        contact.stage = "qualified"
        action = "update_stage"
    elif str(decision.get("qualification_stage") or "") in {"needs_info", "discovery"}:
        contact.stage = "new"
        action = "request_more_info"

    run = IntegrationRun(
        integration_id=integration.id,
        action=action,
        status="ok",
        detail=str(detail),
    )
    integration.last_sync_at = datetime.utcnow()
    session.add(conversation)
    session.add(contact)
    session.add(integration)
    session.add(run)


def schedule_reminder_workflow(
    session: Session,
    *,
    conversation_uuid: str,
    reminder_at: str,
    note: str | None = None,
) -> None:
    integration = _workflow_integration(session)
    run = IntegrationRun(
        integration_id=integration.id,
        action="schedule_reminder",
        status="ok",
        detail=str(
            {
                "conversation_uuid": conversation_uuid,
                "reminder_at": reminder_at,
                "note": note or "",
            }
        ),
    )
    integration.last_sync_at = datetime.utcnow()
    session.add(integration)
    session.add(run)
    session.commit()

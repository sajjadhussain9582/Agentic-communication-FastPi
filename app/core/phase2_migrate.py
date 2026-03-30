"""Path A: add public uuid columns + CRM fields; backfill existing rows (Postgres + SQLite)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import inspect, text
from sqlmodel import Session, select

from app.core.database import engine


def _dialect(engine) -> str:
    return engine.dialect.name


def _has_column(insp, table: str, col: str) -> bool:
    try:
        return any(c["name"] == col for c in insp.get_columns(table))
    except Exception:
        return False


# ensure_schema_phase2 removed - using Alembic now


def backfill_uuids(session: Session) -> None:
    from app.models.contact import Contact
    from app.models.conversation import Conversation
    from app.models.message import Message
    from app.models.form import Form
    from app.models.form_submission import FormSubmission
    from app.models.knowledge_base import KnowledgeBaseEntry
    from app.models.pipeline_stage import PipelineStage

    def ensure_row(m: Any) -> None:
        pu = getattr(m, "public_uuid", None)
        if pu is None or pu == "":
            m.public_uuid = str(uuid.uuid4())
            session.add(m)

    for model in (Contact, Form, FormSubmission, KnowledgeBaseEntry):
        for row in session.exec(select(model)).all():
            ensure_row(row)
    session.commit()

    for c in session.exec(select(Conversation)).all():
        ensure_row(c)
    session.commit()

    conv_by_id = {c.id: c.public_uuid for c in session.exec(select(Conversation)).all() if c.id}

    for m in session.exec(select(Message)).all():
        ensure_row(m)
        cid = m.conversation_id
        if getattr(m, "conversation_public_uuid", None) in (None, "") and cid in conv_by_id:
            m.conversation_public_uuid = conv_by_id[cid]
            session.add(m)
    session.commit()

    # Get stage mapping for backfilling IDs
    all_stages = session.exec(select(PipelineStage)).all()
    stage_map = {s.key: s.id for s in all_stages}

    # Contacts: default tags / external_ids / stage / pipeline_stage_id
    for c in session.exec(select(Contact)).all():
        if getattr(c, "tags", None) is None:
            c.tags = []
        elif isinstance(c.tags, str):
            try:
                c.tags = json.loads(c.tags) if c.tags else []
            except json.JSONDecodeError:
                c.tags = []
        if getattr(c, "external_ids", None) is None:
            c.external_ids = {}
        elif isinstance(c.external_ids, str):
            try:
                c.external_ids = json.loads(c.external_ids) if c.external_ids else {}
            except json.JSONDecodeError:
                c.external_ids = {}
        if not getattr(c, "stage", None):
            c.stage = "new"

        # Sync pipeline_stage string to pipeline_stage_id
        current_p_stage = getattr(c, "pipeline_stage", "lead")
        if current_p_stage in stage_map:
            c.pipeline_stage_id = stage_map[current_p_stage]
            
        session.add(c)
    session.commit()


def ensure_default_integrations(session: Session) -> None:
    from app.models.integration import Integration

    for provider in ("ghl", "email", "sms", "website", "workflow", "calendar", "hubspot"):
        row = session.exec(select(Integration).where(Integration.provider == provider)).first()
        if not row:
            session.add(Integration(provider=provider, status="disconnected"))
    session.commit()

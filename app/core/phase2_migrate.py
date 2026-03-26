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


def ensure_schema_phase2() -> None:
    insp = inspect(engine)
    d = _dialect(engine)

    alters_pg: list[str] = []
    alters_sqlite: list[str] = []

    def add_uuid(table: str):
        if not _has_column(insp, table, "uuid"):
            if d == "postgresql":
                alters_pg.append(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS uuid VARCHAR(36)')
            else:
                alters_sqlite.append(f"ALTER TABLE {table} ADD COLUMN uuid VARCHAR(36)")

    for t in (
        "contacts",
        "conversations",
        "messages",
        "forms",
        "form_submissions",
        "knowledge_base",
    ):
        add_uuid(t)

    if not _has_column(insp, "messages", "conversation_uuid"):
        if d == "postgresql":
            alters_pg.append(
                'ALTER TABLE "messages" ADD COLUMN IF NOT EXISTS conversation_uuid VARCHAR(36)'
            )
        else:
            alters_sqlite.append("ALTER TABLE messages ADD COLUMN conversation_uuid VARCHAR(36)")

    if not _has_column(insp, "contacts", "tags"):
        if d == "postgresql":
            alters_pg.append(
                "ALTER TABLE contacts ADD COLUMN IF NOT EXISTS tags JSONB DEFAULT '[]'::jsonb"
            )
            alters_pg.append(
                "ALTER TABLE contacts ADD COLUMN IF NOT EXISTS external_ids JSONB DEFAULT '{}'::jsonb"
            )
        else:
            alters_sqlite.append("ALTER TABLE contacts ADD COLUMN tags TEXT DEFAULT '[]'")
            alters_sqlite.append("ALTER TABLE contacts ADD COLUMN external_ids TEXT DEFAULT '{}'")

    if not _has_column(insp, "contacts", "notes"):
        if d == "postgresql":
            alters_pg.append("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS notes TEXT")
        else:
            alters_sqlite.append("ALTER TABLE contacts ADD COLUMN notes TEXT")

    if not _has_column(insp, "contacts", "stage"):
        if d == "postgresql":
            alters_pg.append("ALTER TABLE contacts ADD COLUMN IF NOT EXISTS stage VARCHAR(64) DEFAULT 'new'")
        else:
            alters_sqlite.append("ALTER TABLE contacts ADD COLUMN stage VARCHAR(64) DEFAULT 'new'")

    if not _has_column(insp, "knowledge_base", "intent_type"):
        if d == "postgresql":
            alters_pg.append("ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS keywords JSONB DEFAULT '[]'::jsonb")
            alters_pg.append("ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS intent_type VARCHAR(255)")
            alters_pg.append("ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS role_type VARCHAR(255)")
            alters_pg.append("ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS priority INTEGER DEFAULT 0")
            alters_pg.append("ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS tags JSONB DEFAULT '[]'::jsonb")
        else:
            alters_sqlite.append("ALTER TABLE knowledge_base ADD COLUMN keywords TEXT DEFAULT '[]'")
            alters_sqlite.append("ALTER TABLE knowledge_base ADD COLUMN intent_type VARCHAR(255)")
            alters_sqlite.append("ALTER TABLE knowledge_base ADD COLUMN role_type VARCHAR(255)")
            alters_sqlite.append("ALTER TABLE knowledge_base ADD COLUMN priority INTEGER DEFAULT 0")
            alters_sqlite.append("ALTER TABLE knowledge_base ADD COLUMN tags TEXT DEFAULT '[]'")

    with engine.begin() as conn:
        for s in alters_pg + alters_sqlite:
            try:
                conn.execute(text(s))
            except Exception:
                pass  # idempotent / sqlite older

    # ── Phase 2-3: pgvector extension, vector column, match function, index ──
    if d == "postgresql":
        insp = inspect(engine)
        with engine.begin() as conn:
            # 1. Enable pgvector extension
            try:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            except Exception:
                pass

            # 2. Add real vector(1536) column for native similarity search
            if not _has_column(insp, "knowledge_base", "embedding"):
                try:
                    conn.execute(text(
                        "ALTER TABLE knowledge_base ADD COLUMN IF NOT EXISTS embedding vector(1536)"
                    ))
                except Exception:
                    pass

            # 3. Create match_knowledge_base() function for server-side similarity
            try:
                conn.execute(text("""
                    CREATE OR REPLACE FUNCTION match_knowledge_base(
                        query_embedding vector(1536),
                        match_count int DEFAULT 5,
                        filter_category text DEFAULT NULL
                    )
                    RETURNS TABLE (
                        id bigint,
                        question text,
                        answer text,
                        category text,
                        intent_type text,
                        role_type text,
                        priority integer,
                        keywords jsonb,
                        tags jsonb,
                        similarity float
                    )
                    LANGUAGE sql STABLE
                    AS $$
                        SELECT
                            kb.id::bigint,
                            kb.question,
                            kb.answer,
                            kb.category,
                            kb.intent_type,
                            kb.role_type,
                            kb.priority,
                            kb.keywords,
                            kb.tags,
                            1 - (kb.embedding <=> query_embedding) AS similarity
                        FROM knowledge_base kb
                        WHERE kb.embedding IS NOT NULL
                          AND (filter_category IS NULL OR kb.category = filter_category)
                        ORDER BY kb.embedding <=> query_embedding
                        LIMIT match_count;
                    $$;
                """))
            except Exception:
                pass

            # 4. IVFFlat index for fast cosine search
            try:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS knowledge_base_embedding_idx "
                    "ON knowledge_base USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
                ))
            except Exception:
                pass

            try:
                conn.execute(text("ANALYZE knowledge_base"))
            except Exception:
                pass

    # Refresh inspector after DDL
    insp = inspect(engine)


def backfill_uuids(session: Session) -> None:
    from app.models.contact import Contact
    from app.models.conversation import Conversation
    from app.models.message import Message
    from app.models.form import Form
    from app.models.form_submission import FormSubmission
    from app.models.knowledge_base import KnowledgeBaseEntry

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

    # Contacts: default tags / external_ids / stage
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
        session.add(c)
    session.commit()


def ensure_default_integrations(session: Session) -> None:
    from app.models.integration import Integration

    for provider in ("ghl", "email", "sms", "website", "workflow", "calendar"):
        row = session.exec(select(Integration).where(Integration.provider == provider)).first()
        if not row:
            session.add(Integration(provider=provider, status="disconnected"))
    session.commit()

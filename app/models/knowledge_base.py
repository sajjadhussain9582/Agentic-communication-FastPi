from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, String, JSON
from sqlmodel import Field, SQLModel
from typing import Optional, Any

def _new_uuid() -> str:
    return str(uuid.uuid4())

class KnowledgeBaseEntry(SQLModel, table=True):
    """FAQ row for RAG retrieval.

    Primary retrieval uses the pgvector `embedding vector(1536)` column
    via the `match_knowledge_base()` Postgres function.
    The `embedding_json` column is kept for backward compatibility only.
    """

    __tablename__ = "knowledge_base"

    id: Optional[int] = Field(default=None, primary_key=True)
    public_uuid: str = Field(
        default_factory=_new_uuid,
        sa_column=Column("uuid", String(36), unique=True, index=True, nullable=True),
    )
    question: str = Field(default="", index=True)
    answer: str
    category: Optional[str] = Field(default=None, index=True)
    source_type: str = Field(default="faq", index=True) # faq, document
    source_name: Optional[str] = Field(default=None, index=True)
    storage_path: Optional[str] = Field(default=None, index=True)
    chunk_index: Optional[int] = Field(default=None, index=True)
    keywords: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    intent_type: str | None = Field(default=None, index=True)
    role_type: str | None = Field(default=None, index=True)
    priority: int = Field(default=0, index=True)
    tags: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    embedding_json: Optional[str] = None  # DEPRECATED — kept for backward compat; use pgvector `embedding` column
    # Note: the `embedding vector(1536)` column is managed via raw SQL in phase2_migrate.py
    created_at: datetime = Field(default_factory=datetime.utcnow)

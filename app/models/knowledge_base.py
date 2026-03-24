from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, String
from sqlmodel import Field, SQLModel


def _new_uuid() -> str:
    return str(uuid.uuid4())


class KnowledgeBaseEntry(SQLModel, table=True):
    """FAQ row for RAG; embedding stored as JSON string of floats (OpenAI ada-002 dimension 1536)."""

    __tablename__ = "knowledge_base"

    id: Optional[int] = Field(default=None, primary_key=True)
    public_uuid: str = Field(
        default_factory=_new_uuid,
        sa_column=Column("uuid", String(36), unique=True, index=True, nullable=True),
    )
    question: str
    answer: str
    category: Optional[str] = Field(default=None, index=True)
    embedding_json: Optional[str] = None  # JSON array of floats
    created_at: datetime = Field(default_factory=datetime.utcnow)

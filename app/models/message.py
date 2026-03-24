from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Column, JSON, String
from sqlmodel import Field, SQLModel


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    public_uuid: str = Field(
        default_factory=_new_uuid,
        sa_column=Column("uuid", String(36), unique=True, index=True, nullable=True),
    )
    conversation_id: int = Field(foreign_key="conversations.id", index=True)
    conversation_public_uuid: Optional[str] = Field(
        default=None,
        sa_column=Column("conversation_uuid", String(36), nullable=True, index=True),
    )
    sender_type: str  # client, agent, human
    sender_id: Optional[int] = None
    message_type: str = Field(default="text")
    message: str
    channel: str = Field(default="website")
    is_generated: bool = Field(default=False)
    is_handled: bool = Field(default=False)
    rag_source_kb_ids: Optional[list[Any]] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)

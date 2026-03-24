from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Column, JSON, String, Text
from sqlmodel import Field, SQLModel


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Campaign(SQLModel, table=True):
    __tablename__ = "campaigns"

    id: Optional[int] = Field(default=None, primary_key=True)
    public_uuid: str = Field(
        default_factory=_new_uuid,
        sa_column=Column("uuid", String(36), unique=True, index=True, nullable=True),
    )
    name: str
    channel: str = Field(default="email", index=True)
    status: str = Field(default="draft", index=True)  # draft, active, paused, completed
    audience_filter: Optional[dict[str, Any]] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    scheduled_at: Optional[datetime] = None
    sent_count: int = Field(default=0)
    open_count: int = Field(default=0)
    reply_count: int = Field(default=0)
    created_by_id: Optional[int] = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CampaignTarget(SQLModel, table=True):
    __tablename__ = "campaign_targets"

    id: Optional[int] = Field(default=None, primary_key=True)
    public_uuid: str = Field(
        default_factory=_new_uuid,
        sa_column=Column("uuid", String(36), unique=True, index=True, nullable=True),
    )
    campaign_id: int = Field(foreign_key="campaigns.id", index=True)
    contact_id: Optional[int] = Field(default=None, foreign_key="contacts.id", index=True)
    name: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    profession: Optional[str] = None
    status: str = Field(default="pending", index=True)  # pending, sent, failed
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CampaignMessageRow(SQLModel, table=True):
    """Per-target send log for a campaign."""

    __tablename__ = "campaign_messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaigns.id", index=True)
    target_id: int = Field(foreign_key="campaign_targets.id", index=True)
    message_text: str = Field(sa_column=Column(Text, nullable=False))
    send_status: str = Field(default="queued")  # queued, sent, failed
    response_received: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)

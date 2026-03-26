from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import Column, JSON, Numeric, String, Text
from sqlmodel import Field, SQLModel


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Contact(SQLModel, table=True):
    __tablename__ = "contacts"

    id: Optional[int] = Field(default=None, primary_key=True)
    public_uuid: str = Field(
        default_factory=_new_uuid,
        sa_column=Column("uuid", String(36), unique=True, index=True, nullable=True),
    )
    username: Optional[str] = None
    email: Optional[str] = Field(default=None, index=True)
    phone: Optional[str] = None
    company: Optional[str] = None
    source: Optional[str] = None
    status: str = Field(default="new")
    lead_score: Optional[Decimal] = Field(
        default=None, sa_column=Column(Numeric(10, 2), nullable=True)
    )
    budget: Optional[str] = None
    project_type: Optional[str] = None
    timeline: Optional[str] = None
    is_qualified: bool = Field(default=False)
    assigned_user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    tags: list[Any] = Field(default_factory=list, sa_column=Column(JSON))
    notes: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    stage: str = Field(default="new", sa_column=Column(String(64), nullable=True))
    pipeline_stage: str = Field(default="lead", sa_column=Column(String(64), index=True))
    stage_entered_at: datetime = Field(default_factory=datetime.utcnow)
    last_outbound_at: Optional[datetime] = None
    qualification_evidence: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=True)
    )
    external_ids: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=True)
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

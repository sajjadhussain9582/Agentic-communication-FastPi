from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Column, JSON, String
from sqlmodel import Field, SQLModel


def _new_uuid() -> str:
    return str(uuid.uuid4())


class FormSubmission(SQLModel, table=True):
    __tablename__ = "form_submissions"

    id: Optional[int] = Field(default=None, primary_key=True)
    public_uuid: str = Field(
        default_factory=_new_uuid,
        sa_column=Column("uuid", String(36), unique=True, index=True, nullable=True),
    )
    form_id: int = Field(foreign_key="forms.id", index=True)
    contact_id: int = Field(foreign_key="contacts.id", index=True)
    json_response: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)

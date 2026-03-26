from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import Column, JSON, Numeric, String, ForeignKey, Integer
from sqlmodel import Field, SQLModel

class Outcome(SQLModel, table=True):
    __tablename__ = "outcomes"

    id: Optional[int] = Field(default=None, primary_key=True)
    contact_id: int = Field(sa_column=Column(Integer, ForeignKey("contacts.id"), index=True))
    outcome_type: str = Field(sa_column=Column(String(100), index=True))
    value: Optional[Decimal] = Field(
        default=None, sa_column=Column(Numeric(10, 2), nullable=True)
    )
    details: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON, nullable=True)
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)

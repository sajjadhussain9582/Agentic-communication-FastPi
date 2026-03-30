from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, String, Text
from sqlmodel import Field, SQLModel


class PipelineStage(SQLModel, table=True):
    __tablename__ = "pipeline_stages"

    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(sa_column=Column(String(64), unique=True, index=True, nullable=False))
    pipelinestage: str = Field(sa_column=Column(String(100), nullable=False))
    order_index: int = Field(default=0, index=True)
    ai_instructions: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

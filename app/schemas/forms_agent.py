from typing import Any, Optional

from pydantic import BaseModel, Field


class FormCreate(BaseModel):
    title: str
    description: Optional[str] = None
    json_config: dict[str, Any] = Field(default_factory=dict)


class FormRead(BaseModel):
    id: int
    uuid: str
    title: str
    description: Optional[str] = None
    json_config: dict[str, Any]

    class Config:
        from_attributes = True


class KnowledgeBaseCreate(BaseModel):
    question: str
    answer: str
    category: Optional[str] = None


class KnowledgeBaseRead(BaseModel):
    id: int
    uuid: str
    question: str
    answer: str
    category: Optional[str] = None
    has_embedding: bool = False

    class Config:
        from_attributes = True


class MessageRead(BaseModel):
    id: int
    uuid: str
    conversation_uuid: Optional[str] = None
    sender_type: str
    message: str
    channel: str
    is_generated: bool
    rag_source_kb_ids: Optional[list] = None
    created_at: Any

    class Config:
        from_attributes = True


class ConversationDetailRead(BaseModel):
    id: int
    uuid: str
    contact_id: int
    contact_uuid: Optional[str] = None
    channel: str
    status: str
    is_escalated: bool
    last_intent: Optional[str] = None
    qualification_stage: Optional[str] = None
    messages: list[MessageRead] = Field(default_factory=list)

    class Config:
        from_attributes = True

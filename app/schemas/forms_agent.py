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
    question: str = ""
    answer: str
    category: Optional[str] = None
    source_type: str = "faq"
    source_name: Optional[str] = None
    storage_path: Optional[str] = None


class KnowledgeBaseRead(BaseModel):
    id: int
    uuid: str
    question: str
    answer: str
    category: Optional[str] = None
    source_type: str = "faq"
    source_name: Optional[str] = None
    storage_path: Optional[str] = None
    chunk_index: Optional[int] = None
    has_embedding: bool = False

    class Config:
        from_attributes = True

class KnowledgeBasePasteRequest(BaseModel):
    content: str
    source_name: str
    category: Optional[str] = None

class KnowledgeBaseUploadResponse(BaseModel):
    ok: bool
    source_name: str
    chunks_created: int

class KnowledgeBaseListResponse(BaseModel):
    items: list[KnowledgeBaseRead]
    total: int
    skip: int
    limit: int


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
    escalation_brief: Optional[str] = None
    last_intent: Optional[str] = None
    qualification_stage: Optional[str] = None
    messages: list[MessageRead] = Field(default_factory=list)

    class Config:
        from_attributes = True

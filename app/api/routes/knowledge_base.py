from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.api.deps.auth import get_current_user
from app.db.session import get_session
from app.models.knowledge_base import KnowledgeBaseEntry
from app.models.user import User
from app.schemas.forms_agent import KnowledgeBaseCreate, KnowledgeBaseRead
from app.services.kb_rag import ensure_entry_embedding

router = APIRouter(prefix="/knowledge-base", tags=["Knowledge base (RAG)"])


@router.get("", response_model=list[KnowledgeBaseRead])
def list_kb(
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    rows = list(session.exec(select(KnowledgeBaseEntry)).all())
    return [
        KnowledgeBaseRead(
            id=r.id,
            uuid=r.public_uuid,
            question=r.question,
            answer=r.answer,
            category=r.category,
            has_embedding=bool(r.embedding_json),
        )
        for r in rows
    ]


@router.post("", response_model=KnowledgeBaseRead)
def create_kb(
    body: KnowledgeBaseCreate,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    e = KnowledgeBaseEntry(
        question=body.question.strip(),
        answer=body.answer.strip(),
        category=body.category,
    )
    session.add(e)
    session.commit()
    session.refresh(e)
    try:
        ensure_entry_embedding(session, e)
    except Exception:
        pass
    session.refresh(e)
    return KnowledgeBaseRead(
        id=e.id,
        uuid=e.public_uuid,
        question=e.question,
        answer=e.answer,
        category=e.category,
        has_embedding=bool(e.embedding_json),
    )


@router.patch("/{entry_id}", response_model=KnowledgeBaseRead)
def update_kb(
    entry_id: int,
    body: KnowledgeBaseCreate,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    e = session.get(KnowledgeBaseEntry, entry_id)
    if not e:
        raise HTTPException(404, "Not found")
    e.question = body.question.strip()
    e.answer = body.answer.strip()
    e.category = body.category
    e.embedding_json = None
    session.add(e)
    session.commit()
    try:
        ensure_entry_embedding(session, e)
    except Exception:
        pass
    session.refresh(e)
    return KnowledgeBaseRead(
        id=e.id,
        uuid=e.public_uuid,
        question=e.question,
        answer=e.answer,
        category=e.category,
        has_embedding=bool(e.embedding_json),
    )


@router.delete("/{entry_id}")
def delete_kb(
    entry_id: int,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    e = session.get(KnowledgeBaseEntry, entry_id)
    if not e:
        raise HTTPException(404, "Not found")
    session.delete(e)
    session.commit()
    return {"ok": True}

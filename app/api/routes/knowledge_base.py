from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlmodel import Session, select

from app.api.deps.auth import get_current_user
from app.db.session import get_session
from app.models.knowledge_base import KnowledgeBaseEntry
from app.models.user import User
from app.schemas.forms_agent import (
    KnowledgeBaseCreate, 
    KnowledgeBaseRead, 
    KnowledgeBasePasteRequest,
    KnowledgeBaseUploadResponse,
    KnowledgeBaseListResponse
)
from app.services.kb_rag import ensure_entry_embedding
from app.services.kb_ingestion import process_text_content, extract_text_from_pdf
from app.services.supabase_storage import upload_to_supabase, delete_from_supabase

router = APIRouter(prefix="/knowledge-base", tags=["Knowledge base (RAG)"])


@router.get("", response_model=KnowledgeBaseListResponse)
def list_kb(
    skip: int = 0,
    limit: int = 100,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """List knowledge base entries with pagination."""
    # Build query
    stmt = select(KnowledgeBaseEntry)
    
    # Get total count before pagination
    from sqlalchemy import func
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    
    # Apply skip/limit
    rows = list(session.exec(stmt.offset(skip).limit(limit)).all())
    
    items = [
        KnowledgeBaseRead(
            id=r.id,
            uuid=r.public_uuid,
            question=r.question,
            answer=r.answer,
            category=r.category,
            source_type=r.source_type,
            source_name=r.source_name,
            storage_path=r.storage_path,
            chunk_index=r.chunk_index,
            has_embedding=bool(r.embedding_json or r.id),
        )
        for r in rows
    ]
    return KnowledgeBaseListResponse(
        items=items,
        total=total,
        skip=skip,
        limit=limit
    )


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
        source_type=e.source_type,
        source_name=e.source_name,
        storage_path=e.storage_path,
        chunk_index=e.chunk_index,
        has_embedding=bool(e.embedding_json or e.id), # Check ID since vector is raw SQL
    )

@router.post("/upload", response_model=KnowledgeBaseUploadResponse)
async def upload_kb_file(
    file: UploadFile = File(...),
    category: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Uploaded a PDF/CSV file, stores it in Supabase, and ingest it into RAG."""
    content_type = file.content_type
    filename = file.filename
    
    # 0. Validation: Reject unsupported binary types (like .doc)
    allowed_types = ["application/pdf", "text/csv", "text/plain"]
    if content_type not in allowed_types:
        # Fallback check by extension just in case
        ext = filename.split(".")[-1].lower() if "." in filename else ""
        if ext not in ["pdf", "csv", "txt"]:
            raise HTTPException(
                status_code=400, 
                detail=f"Unsupported file type: {content_type}. Only PDF, CSV, and TXT are supported."
            )

    file_bytes = await file.read()
    
    # 1. Upload to Supabase Bucket
    timestamp = int(datetime.utcnow().timestamp())
    storage_path = f"{current_user.id}/{timestamp}-{filename}"
    try:
        storage_path = upload_to_supabase(file_bytes, storage_path, content_type)
    except Exception as e:
        raise HTTPException(500, f"Storage upload failed: {str(e)}")
        
    # 2. Extract Text (if PDF)
    # Temporary save for PyPDF2
    import tempfile
    import os
    text_content = ""
    if content_type == "application/pdf":
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            text_content = extract_text_from_pdf(tmp_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
    elif "text/csv" in content_type:
        text_content = file_bytes.decode("utf-8", errors="ignore")
    else:
        text_content = file_bytes.decode("utf-8", errors="ignore")

    # 3. Process & Ingest
    entries = process_text_content(
        session=session,
        content=text_content,
        source_name=filename,
        source_type="document",
        category=category,
        storage_path=storage_path
    )
    
    return KnowledgeBaseUploadResponse(
        ok=True,
        source_name=filename,
        chunks_created=len(entries)
    )

@router.post("/paste", response_model=KnowledgeBaseUploadResponse)
async def paste_kb_text(
    body: KnowledgeBasePasteRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Ingests raw pasted text into the RAG system."""
    entries = process_text_content(
        session=session,
        content=body.content,
        source_name=body.source_name,
        source_type="document",
        category=body.category
    )
    
    return KnowledgeBaseUploadResponse(
        ok=True,
        source_name=body.source_name,
        chunks_created=len(entries)
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


@router.delete("/source/{source_name}")
def delete_kb_source(
    source_name: str,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Deletes all chunks associated with a specific source name and removes file from storage."""
    stmt = select(KnowledgeBaseEntry).where(KnowledgeBaseEntry.source_name == source_name)
    entries = session.exec(stmt).all()
    
    if not entries:
        raise HTTPException(404, f"No entries found for source: {source_name}")
    
    # 1. Identify storage path (they should all have the same one if from same upload)
    storage_path = next((e.storage_path for e in entries if e.storage_path), None)
    
    # 2. Delete all chunks from DB
    count = 0
    for e in entries:
        session.delete(e)
        count += 1
    
    session.commit()
    
    # 3. Delete from Supabase Storage if path exists
    if storage_path:
        try:
            delete_from_supabase(storage_path)
        except Exception as e:
            logger.error(f"Cleanup: Failed to delete storage file {storage_path}: {e}")
            
    return {"ok": True, "deleted_count": count, "storage_deleted": bool(storage_path)}

@router.delete("/{entry_id}")
def delete_kb(
    entry_id: int,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    e = session.get(KnowledgeBaseEntry, entry_id)
    if not e:
        raise HTTPException(404, "Not found")
    
    storage_path = e.storage_path
    
    session.delete(e)
    session.commit()
    
    # If this was the last chunk of a file, we might want to delete from storage.
    # But to be safe, if a single entry is deleted, we don't delete the whole file unless it's unique.
    # However, if it's an FAQ (no storage_path), there's nothing to do.
    # If it's a document chunk and user deletes it, we keep the file unless source is gone.
    # For now, let's only delete from storage in 'delete_kb_source'.
    
    return {"ok": True}

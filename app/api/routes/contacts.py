import re
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, desc, select

from app.api.deps.auth import get_current_user
from app.db.session import get_session
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.pipeline_stage import PipelineStage
from app.models.user import User

router = APIRouter(prefix="/contacts", tags=["Contacts"])

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)


class ContactListItem(BaseModel):
    uuid: str
    email: Optional[str] = None
    username: Optional[str] = None
    company: Optional[str] = None
    stage: str
    status: str
    tags: list[Any] = Field(default_factory=list)
    updated_at: Any


class ContactDetailRead(BaseModel):
    uuid: str
    id: int
    email: Optional[str] = None
    username: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    source: Optional[str] = None
    status: str
    stage: str
    tags: list[Any] = Field(default_factory=list)
    notes: Optional[str] = None
    external_ids: dict[str, Any] = Field(default_factory=dict)
    lead_score: Optional[str] = None
    budget: Optional[str] = None
    project_type: Optional[str] = None
    timeline: Optional[str] = None
    is_qualified: bool
    assigned_user_id: Optional[int] = None
    conversation_uuids: list[str] = Field(default_factory=list)
    created_at: Any
    updated_at: Any


class ContactPatchBody(BaseModel):
    stage: Optional[str] = None
    tags: Optional[list[Any]] = None
    notes: Optional[str] = None
    assigned_user_id: Optional[int] = None
    external_ids: Optional[dict[str, Any]] = None
    status: Optional[str] = None


@router.get("", response_model=list[ContactListItem])
def list_contacts(
    search: Optional[str] = Query(None),
    cursor: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    channel: Optional[str] = Query(None, description="Filter by source containing this channel"),
    stage: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    q = select(Contact).order_by(desc(Contact.updated_at), desc(Contact.id))
    if search:
        from sqlalchemy import or_

        s = f"%{search.strip()}%"
        q = q.where(
            or_(
                Contact.email.ilike(s),
                Contact.username.ilike(s),
                Contact.company.ilike(s),
            )
        )
    if channel:
        q = q.where(Contact.source.contains(channel))
    if stage:
        q = q.where(Contact.stage == stage)
    if cursor:
        try:
            cid = int(cursor)
            c0 = session.get(Contact, cid)
            if c0:
                q = q.where(
                    (Contact.updated_at < c0.updated_at)
                    | ((Contact.updated_at == c0.updated_at) & (Contact.id < c0.id))
                )
        except ValueError:
            pass
    rows = list(session.exec(q.limit(limit * 3 if tag else limit)).all())
    out: list[ContactListItem] = []
    for c in rows:
        tags = c.tags if isinstance(c.tags, list) else []
        if tag and tag not in [str(t) for t in tags]:
            continue
        out.append(
            ContactListItem(
                uuid=c.public_uuid,
                email=c.email,
                username=c.username,
                company=c.company,
                stage=c.stage or "new",
                status=c.status,
                tags=tags,
                updated_at=c.updated_at,
            )
        )
        if len(out) >= limit:
            break
    return out


@router.get("/kanban")
def get_kanban_leads(
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    # Fetch all active stages from DB
    stages = list(session.exec(select(PipelineStage).order_by(PipelineStage.order_index)).all())
    contacts = session.exec(select(Contact).order_by(desc(Contact.updated_at))).all()

    # Dynamic grouping
    kanban_data = {s.key: [] for s in stages}

    for c in contacts:
        item = {
            "uuid": c.public_uuid,
            "email": c.email,
            "username": c.username,
            "company": c.company,
            "stage": c.pipeline_stage,
            "status": c.status,
            "lead_score": float(c.lead_score) if c.lead_score else 0,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }

        stage_key = c.pipeline_stage or "discovery"
        if stage_key in kanban_data:
            kanban_data[stage_key].append(item)
        else:
            # Fallback if lead has an invalid stage string
            if "discovery" in kanban_data:
                kanban_data["discovery"].append(item)

    return kanban_data


def _contact_detail(session: Session, c: Contact) -> ContactDetailRead:
    convs = list(
        session.exec(select(Conversation).where(Conversation.contact_id == c.id)).all()
    )
    tags = c.tags if isinstance(c.tags, list) else []
    ext = c.external_ids if isinstance(c.external_ids, dict) else {}
    return ContactDetailRead(
        uuid=c.public_uuid,
        id=c.id,
        email=c.email,
        username=c.username,
        phone=c.phone,
        company=c.company,
        source=c.source,
        status=c.status,
        stage=c.stage or "new",
        tags=tags,
        notes=c.notes,
        external_ids=ext,
        lead_score=str(c.lead_score) if c.lead_score is not None else None,
        budget=c.budget,
        project_type=c.project_type,
        timeline=c.timeline,
        is_qualified=c.is_qualified,
        assigned_user_id=c.assigned_user_id,
        conversation_uuids=[x.public_uuid for x in convs],
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


@router.get("/{contact_uuid}", response_model=ContactDetailRead)
def get_contact(
    contact_uuid: str,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not UUID_RE.match(contact_uuid.strip()):
        raise HTTPException(400, "contact_uuid must be a UUID")
    c = session.exec(select(Contact).where(Contact.public_uuid == contact_uuid.strip())).first()
    if not c:
        raise HTTPException(404, "Contact not found")
    return _contact_detail(session, c)


@router.patch("/{contact_uuid}", response_model=ContactDetailRead)
def patch_contact(
    contact_uuid: str,
    body: ContactPatchBody,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not UUID_RE.match(contact_uuid.strip()):
        raise HTTPException(400, "contact_uuid must be a UUID")
    c = session.exec(select(Contact).where(Contact.public_uuid == contact_uuid.strip())).first()
    if not c:
        raise HTTPException(404, "Contact not found")
    if body.stage is not None:
        # If human changes stage, update both the string and the ID link
        c.stage = body.stage[:200]
        c.pipeline_stage = body.stage
        stage_record = session.exec(
            select(PipelineStage).where(PipelineStage.key == body.stage)
        ).first()
        if stage_record:
            c.pipeline_stage_id = stage_record.id
        c.stage_entered_at = datetime.utcnow()
    if body.tags is not None:
        c.tags = body.tags
    if body.notes is not None:
        c.notes = body.notes[:50000] if body.notes else None
    if body.assigned_user_id is not None:
        c.assigned_user_id = body.assigned_user_id
    if body.external_ids is not None:
        base = c.external_ids if isinstance(c.external_ids, dict) else {}
        base.update(body.external_ids)
        c.external_ids = base
    if body.status is not None:
        c.status = body.status[:64]
    c.updated_at = datetime.utcnow()
    session.add(c)
    session.commit()
    session.refresh(c)
    return _contact_detail(session, c)

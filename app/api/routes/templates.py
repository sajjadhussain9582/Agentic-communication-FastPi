from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, desc, select

from app.api.deps.auth import get_current_user
from app.db.session import get_session
from app.models.template import MessageTemplate
from app.models.user import User

router = APIRouter(prefix="/templates", tags=["Templates"])


class TemplateRead(BaseModel):
    uuid: str
    id: int
    name: str
    channel: str
    body: str
    created_at: Any


class TemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    channel: str = Field(default="email")
    body: str = Field(..., min_length=1, max_length=50000)


@router.get("", response_model=list[TemplateRead])
def list_templates(
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    rows = list(session.exec(select(MessageTemplate).order_by(desc(MessageTemplate.created_at))).all())
    return [
        TemplateRead(
            uuid=r.public_uuid,
            id=r.id,
            name=r.name,
            channel=r.channel,
            body=r.body,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("", response_model=TemplateRead)
def create_template(
    body: TemplateCreate,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    t = MessageTemplate(name=body.name.strip(), channel=body.channel[:50], body=body.body)
    session.add(t)
    session.commit()
    session.refresh(t)
    return TemplateRead(
        uuid=t.public_uuid,
        id=t.id,
        name=t.name,
        channel=t.channel,
        body=t.body,
        created_at=t.created_at,
    )


@router.get("/{template_id}", response_model=TemplateRead)
def get_template(
    template_id: int,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    t = session.get(MessageTemplate, template_id)
    if not t:
        raise HTTPException(404, "Template not found")
    return TemplateRead(
        uuid=t.public_uuid,
        id=t.id,
        name=t.name,
        channel=t.channel,
        body=t.body,
        created_at=t.created_at,
    )

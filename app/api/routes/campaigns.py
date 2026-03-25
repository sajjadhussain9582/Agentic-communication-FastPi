import re
import uuid
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, desc, select

from app.api.deps.auth import get_current_user
from app.db.session import get_session
from app.models.campaign import Campaign, CampaignMessageRow, CampaignTarget
from app.models.contact import Contact
from app.models.user import User
from app.services.channel_delivery import deliver_message

router = APIRouter(prefix="/campaigns", tags=["Campaigns"])

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)


class CampaignRead(BaseModel):
    uuid: str
    id: int
    name: str
    channel: str
    status: str
    scheduled_at: Optional[datetime] = None
    sent_count: int
    open_count: int
    reply_count: int
    created_at: Any
    updated_at: Any


class CampaignDetailRead(CampaignRead):
    audience_filter: Optional[dict[str, Any]] = None
    targets_preview: list[dict[str, Any]] = Field(default_factory=list)


class CampaignCreateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=500)
    channel: str = Field(default="email")
    audience_filter: Optional[dict[str, Any]] = None
    scheduled_at: Optional[datetime] = None


class CampaignPatchBody(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None  # draft, active, paused, completed
    scheduled_at: Optional[datetime] = None
    audience_filter: Optional[dict[str, Any]] = None


def _camp_read(c: Campaign) -> CampaignRead:
    return CampaignRead(
        uuid=c.public_uuid,
        id=c.id,
        name=c.name,
        channel=c.channel,
        status=c.status,
        scheduled_at=c.scheduled_at,
        sent_count=c.sent_count,
        open_count=c.open_count,
        reply_count=c.reply_count,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


@router.get("", response_model=list[CampaignRead])
def list_campaigns(
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    rows = list(session.exec(select(Campaign).order_by(desc(Campaign.updated_at))).all())
    return [_camp_read(c) for c in rows]


@router.get("/{campaign_uuid}", response_model=CampaignDetailRead)
def get_campaign(
    campaign_uuid: str,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not UUID_RE.match(campaign_uuid.strip()):
        raise HTTPException(400, "Invalid campaign UUID")
    c = session.exec(select(Campaign).where(Campaign.public_uuid == campaign_uuid.strip())).first()
    if not c:
        raise HTTPException(404, "Campaign not found")
    targets = list(
        session.exec(select(CampaignTarget).where(CampaignTarget.campaign_id == c.id).limit(20)).all()
    )
    prev = [
        {"uuid": t.public_uuid, "email": t.email, "name": t.name, "status": t.status}
        for t in targets
    ]
    base = _camp_read(c)
    return CampaignDetailRead(
        **base.model_dump(),
        audience_filter=c.audience_filter,
        targets_preview=prev,
    )


@router.post("", response_model=CampaignRead)
def create_campaign(
    body: CampaignCreateBody,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    c = Campaign(
        name=body.name.strip(),
        channel=body.channel[:50],
        status="draft",
        audience_filter=body.audience_filter,
        scheduled_at=body.scheduled_at,
        created_by_id=user.id,
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    return _camp_read(c)


@router.patch("/{campaign_uuid}", response_model=CampaignRead)
def patch_campaign(
    campaign_uuid: str,
    body: CampaignPatchBody,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    if not UUID_RE.match(campaign_uuid.strip()):
        raise HTTPException(400, "Invalid campaign UUID")
    c = session.exec(select(Campaign).where(Campaign.public_uuid == campaign_uuid.strip())).first()
    if not c:
        raise HTTPException(404, "Campaign not found")
    if body.name is not None:
        c.name = body.name.strip()[:500]
    if body.status is not None:
        c.status = body.status[:32]
    if body.scheduled_at is not None:
        c.scheduled_at = body.scheduled_at
    if body.audience_filter is not None:
        c.audience_filter = body.audience_filter
    c.updated_at = datetime.utcnow()
    session.add(c)
    session.commit()
    session.refresh(c)
    return _camp_read(c)


@router.post("/{campaign_uuid}/send")
async def send_campaign(
    campaign_uuid: str,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Phase 2 stub: mark send queued, bump counters; delivery adapter in Phase 2.5."""
    if not UUID_RE.match(campaign_uuid.strip()):
        raise HTTPException(400, "Invalid campaign UUID")
    c = session.exec(select(Campaign).where(Campaign.public_uuid == campaign_uuid.strip())).first()
    if not c:
        raise HTTPException(404, "Campaign not found")
    targets = list(session.exec(select(CampaignTarget).where(CampaignTarget.campaign_id == c.id)).all())
    if not targets:
        # Build targets from contacts using simple audience filters.
        contact_q = select(Contact)
        af = c.audience_filter or {}
        if isinstance(af, dict):
            stage = af.get("stage")
            if stage:
                contact_q = contact_q.where(Contact.stage == str(stage))
            status = af.get("status")
            if status:
                contact_q = contact_q.where(Contact.status == str(status))
        contacts = list(session.exec(contact_q.limit(100)).all())
        for ct in contacts:
            t = CampaignTarget(
                campaign_id=c.id,
                contact_id=ct.id,
                name=ct.username,
                email=ct.email,
                company=ct.company,
                profession=ct.project_type,
                status="pending",
            )
            session.add(t)
        session.commit()
        targets = list(session.exec(select(CampaignTarget).where(CampaignTarget.campaign_id == c.id)).all())
    if not targets:
        # fallback synthetic target
        t = CampaignTarget(
            campaign_id=c.id,
            name="Sample",
            email="pending@example.com",
            status="pending",
        )
        session.add(t)
        session.commit()
        session.refresh(t)
        targets = [t]
    n = 0
    for t in targets:
        if t.status == "replied":
            continue
        personalized = f"Hi {t.name or 'there'}, we'd like to discuss a partnership opportunity."
        row = CampaignMessageRow(
            campaign_id=c.id,
            target_id=t.id,
            message_text=personalized,
            send_status="sent",
        )
        await deliver_message(
            session,
            channel=c.channel,
            recipient=t.email,
            message=personalized,
            metadata={"campaign_uuid": c.public_uuid, "target_uuid": t.public_uuid},
        )
        # Queue follow-up unless already replied.
        followup = CampaignMessageRow(
            campaign_id=c.id,
            target_id=t.id,
            message_text=f"Follow-up: checking if you are open to a short partnership call, {t.name or 'team'}.",
            send_status="queued",
        )
        session.add(followup)
        session.add(row)
        t.status = "sent"
        session.add(t)
        n += 1
    c.sent_count += n
    c.status = "active"
    c.updated_at = datetime.utcnow()
    session.add(c)
    session.commit()
    job_id = str(uuid.uuid4())
    return {
        "status": "queued",
        "job_id": job_id,
        "targets_enqueued": n,
        "message": "Delivery adapter not wired; messages logged as queued.",
    }

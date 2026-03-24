from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from app.api.deps.auth import get_current_user
from app.core.config import settings
from app.db.session import get_session
from app.models.integration import Integration, IntegrationRun
from app.models.user import User

router = APIRouter(prefix="/integrations", tags=["Integrations"])


class IntegrationRead(BaseModel):
    uuid: str
    provider: str
    status: str
    last_sync_at: Optional[datetime] = None
    last_error: Optional[str] = None


class IntegrationRunRead(BaseModel):
    id: int
    action: str
    status: str
    detail: Optional[str] = None
    created_at: datetime


@router.get("", response_model=list[IntegrationRead])
def list_integrations(
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    rows = list(session.exec(select(Integration)).all())
    return [
        IntegrationRead(
            uuid=r.public_uuid,
            provider=r.provider,
            status=r.status,
            last_sync_at=r.last_sync_at,
            last_error=r.last_error,
        )
        for r in rows
    ]


@router.post("/{provider}/connect")
def connect_integration(
    provider: str,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    row = session.exec(select(Integration).where(Integration.provider == provider)).first()
    if not row:
        row = Integration(provider=provider, status="pending")
        session.add(row)
        session.commit()
        session.refresh(row)
    base = (settings.PUBLIC_APP_URL or "").strip() or "https://app.example.com"
    oauth_url = f"{base}/oauth/{provider}/authorize?integration={row.public_uuid}"
    run = IntegrationRun(
        integration_id=row.id,
        action="connect",
        status="initiated",
        detail="OAuth URL issued (stub)",
    )
    session.add(run)
    session.commit()
    return {
        "provider": provider,
        "oauth_url": oauth_url,
        "instructions": f"Complete OAuth at {oauth_url} (stub until provider credentials are configured).",
    }


@router.post("/{provider}/disconnect")
def disconnect_integration(
    provider: str,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    row = session.exec(select(Integration).where(Integration.provider == provider)).first()
    if not row:
        raise HTTPException(404, "Integration not found")
    row.status = "disconnected"
    row.config_json = None
    row.last_error = None
    row.updated_at = datetime.utcnow()
    session.add(row)
    run = IntegrationRun(
        integration_id=row.id,
        action="disconnect",
        status="ok",
        detail=None,
    )
    session.add(run)
    session.commit()
    return {"ok": True, "provider": provider, "status": row.status}


@router.post("/{provider}/sync")
def sync_integration(
    provider: str,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    row = session.exec(select(Integration).where(Integration.provider == provider)).first()
    if not row:
        row = Integration(provider=provider, status="connected")
    row.status = "connected"
    row.last_sync_at = datetime.utcnow()
    row.last_error = None
    session.add(row)
    session.commit()
    session.refresh(row)
    session.add(
        IntegrationRun(
            integration_id=row.id,
            action="sync",
            status="ok",
            detail=f"{provider} sync completed (stub)",
        )
    )
    session.commit()
    return {"ok": True, "provider": provider, "status": row.status, "last_sync_at": row.last_sync_at}


@router.get("/{provider}/runs", response_model=list[IntegrationRunRead])
def list_integration_runs(
    provider: str,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    row = session.exec(select(Integration).where(Integration.provider == provider)).first()
    if not row:
        raise HTTPException(404, "Integration not found")
    runs = list(
        session.exec(
            select(IntegrationRun)
            .where(IntegrationRun.integration_id == row.id)
            .order_by(IntegrationRun.id.desc())
            .limit(100)
        ).all()
    )
    return [
        IntegrationRunRead(
            id=r.id,
            action=r.action,
            status=r.status,
            detail=r.detail,
            created_at=r.created_at,
        )
        for r in runs
    ]

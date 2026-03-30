import logging
import urllib.parse
from email.utils import parseaddr
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

from app.api.deps.auth import get_current_user
from app.core.config import settings
from app.db.session import get_session
from app.models.integration import Integration, IntegrationRun
from app.models.user import User
from app.schemas.integration import EmailConfigSMTP, EmailConfigResend, CalendlyConfig
from app.services.integrations.hubspot_client import HubSpotClient

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


@router.post("/email/configure")
def configure_email(
    config: EmailConfigSMTP | EmailConfigResend,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    row = session.exec(select(Integration).where(Integration.provider == "email")).first()
    if not row:
        row = Integration(provider="email")
    
    row.config_json = config.model_dump()
    row.status = "connected"
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.flush() # Ensure ID is populated
    
    run = IntegrationRun(
        integration_id=row.id,
        action="configure_email",
        status="ok",
        detail=f"Email configured as {config.type}",
    )
    session.add(run)
    session.commit()
    return {"ok": True, "provider": "email", "status": row.status}


@router.post("/calendly/configure")
def configure_calendly(
    config: CalendlyConfig,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    row = session.exec(select(Integration).where(Integration.provider == "scheduling")).first()
    if not row:
        row = Integration(provider="scheduling")
    
    row.config_json = config.model_dump()
    row.status = "connected"
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.flush() # Ensure ID is populated
    
    run = IntegrationRun(
        integration_id=row.id,
        action="configure_calendly",
        status="ok",
        detail=f"Calendly configured as {config.type}",
    )
    session.add(run)
    session.commit()
    return {"ok": True, "provider": "scheduling", "status": row.status}


@router.get("/calendly/event-types")
async def get_calendly_event_types(
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    row = session.exec(select(Integration).where(Integration.provider == "scheduling")).first()
    if not row or not row.config_json or "token" not in row.config_json:
        raise HTTPException(400, "Calendly not configured with PAT")
    
    token = row.config_json["token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    async with httpx.AsyncClient() as client:
        # 1. Get current user info to get URI
        try:
            user_res = await client.get("https://api.calendly.com/users/me", headers=headers)
            user_res.raise_for_status()
            user_uri = user_res.json()["resource"]["uri"]
            
            # 2. Get event types for this user
            events_res = await client.get(
                f"https://api.calendly.com/event_types?user={user_uri}&active=true", 
                headers=headers
            )
            events_res.raise_for_status()
            return events_res.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(e.response.status_code, f"Calendly API error: {e.response.text}")
        except Exception as e:
            raise HTTPException(500, f"Internal error: {str(e)}")


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


@router.get("/hubspot/authorize")
def authorize_hubspot():
    """Redirects the user to HubSpot's OAuth consent screen."""
    if not settings.HUBSPOT_CLIENT_ID:
        raise HTTPException(400, "HUBSPOT_CLIENT_ID not configured")

    scopes = [
        "crm.objects.contacts.read",
        "crm.objects.contacts.write",
        "crm.objects.owners.read",
        "crm.schemas.contacts.read",
        "crm.schemas.contacts.write",

    ]
    
    params = {
        "client_id": settings.HUBSPOT_CLIENT_ID,
        "redirect_uri": settings.HUBSPOT_REDIRECT_URI,
        "scope": " ".join(scopes),
    }
    
    auth_url = "https://app.hubspot.com/oauth/authorize?" + urllib.parse.urlencode(params)
    return RedirectResponse(auth_url)


@router.get("/hubspot/callback")
async def hubspot_callback(
    code: str,
    session: Session = Depends(get_session),
):
    """Handles the OAuth callback from HubSpot."""
    client = HubSpotClient(session)
    try:
        await client.exchange_code_for_tokens(code)
        # Redirect back to the frontend integration page
        frontend_url = settings.FRONTEND_URL or "http://localhost:3000"
        return RedirectResponse(f"{frontend_url}/settings/integrations?status=success&provider=hubspot")
    except Exception as e:
        logger.error(f"HubSpot OAuth error: {str(e)}")
        frontend_url = settings.FRONTEND_URL or "http://localhost:3000"
        return RedirectResponse(f"{frontend_url}/settings/integrations?status=error&provider=hubspot&message={urllib.parse.quote(str(e))}")


@router.post("/hubspot/sync/{contact_uuid}")
async def sync_contact_to_hubspot_manual(
    contact_uuid: str,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    """Manually push a specific contact to HubSpot."""
    from app.models.contact import Contact
    contact = session.exec(select(Contact).where(Contact.public_uuid == contact_uuid)).first()
    if not contact:
        raise HTTPException(404, "Contact not found")

    if not contact.email:
        raise HTTPException(400, "Contact must have an email to sync to HubSpot")

    # Extract clean email (handle "Name <email@..." format)
    _, clean_email = parseaddr(contact.email)
    if not clean_email:
        raise HTTPException(400, f"No valid email address found in '{contact.email}'")

    # HubSpot property mappings
    STATUS_MAP = {
        "new": "NEW",
        "active": "OPEN",
        "stale": "ATTEMPTED_TO_CONTACT",
        "qualified": "OPEN_DEAL",
        "unqualified": "UNQUALIFIED",
        "lost": "UNQUALIFIED"
    }
    
    STAGE_MAP = {
        "discovery": "lead",
        "qualified": "marketingqualifiedlead",
        "proposal_ready": "salesqualifiedlead",
        "negotiation": "opportunity",
        "won": "customer",
        "lost": "other"
    }

    hs_status = STATUS_MAP.get((contact.status or "new").lower(), "NEW")
    hs_lifecycle = STAGE_MAP.get((contact.pipeline_stage or "discovery").lower(), "lead")

    hubspot = HubSpotClient(session)
    props = {
        "email": clean_email,
        "firstname": contact.username.split(" ")[0] if contact.username else "Unknown",
        "lastname": " ".join(contact.username.split(" ")[1:]) if contact.username and " " in contact.username else "",
        "phone": contact.phone,
        "company": contact.company,
        "lifecyclestage": hs_lifecycle,
        "hs_lead_status": hs_status
    }

    try:
        res = await hubspot.upsert_contact(clean_email, props)
        if res.get("ok"):
            results = res.get("data", {}).get("results", [])
            if results:
                hs_id = results[0].get("id")
                if not contact.external_ids:
                    contact.external_ids = {}
                contact.external_ids["hubspot_id"] = hs_id
                session.add(contact)
                session.commit()
                return {"ok": True, "hubspot_id": hs_id}
        else:
            raise HTTPException(500, f"HubSpot sync failed: {res.get('error')}")
    except Exception as e:
        logger.error(f"HubSpot manual sync error: {str(e)}")
        raise HTTPException(500, f"Internal error during sync: {str(e)}")

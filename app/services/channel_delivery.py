"""Channel delivery adapters (stubbed providers with auditable logs)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from app.models.integration import Integration, IntegrationRun


def _ensure_provider(session: Session, provider: str) -> Integration:
    row = session.exec(select(Integration).where(Integration.provider == provider)).first()
    if row:
        return row
    row = Integration(provider=provider, status="connected")
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def deliver_message(
    session: Session,
    *,
    channel: str,
    recipient: str | None,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Best-effort delivery adapter with IntegrationRun audit log."""
    ch = (channel or "website").strip().lower()
    provider = ch if ch in ("email", "sms", "website", "message") else "website"
    integration = _ensure_provider(session, provider)
    ok = bool(recipient) or provider in ("website", "message")
    detail = {
        "channel": ch,
        "recipient": recipient,
        "preview": (message or "")[:200],
        "metadata": metadata or {},
    }
    run = IntegrationRun(
        integration_id=integration.id,
        action=f"outbound_{provider}",
        status="sent" if ok else "failed",
        detail=str(detail),
    )
    integration.last_sync_at = datetime.utcnow()
    integration.last_error = None if ok else "missing recipient"
    integration.status = "connected" if ok else integration.status
    session.add(integration)
    session.add(run)
    session.commit()
    return {"ok": ok, "provider": provider, "reason": None if ok else "missing recipient"}

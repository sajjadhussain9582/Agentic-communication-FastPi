import httpx
import logging
from typing import Optional
from datetime import datetime
from sqlmodel import Session, select
from app.models.integration import Integration

logger = logging.getLogger(__name__)

class CalendlyClient:
    def __init__(self, session: Session):
        integration = session.exec(
            select(Integration).where(Integration.provider == "scheduling").where(Integration.status == "connected")
        ).first()
        if not integration:
            raise ValueError("Scheduling integration not found or not connected")
        config = integration.config_json or {}
        self.access_token = config.get("token")
        if not self.access_token:
            raise ValueError("Scheduling access token not configured")
        
        # We need the full user URI, not just 'me'
        self.user_uri = config.get("user_uri")
        self.client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=30.0
        )

    async def _resolve_user_uri(self) -> str:
        """Resolve 'me' to the actual user URI if needed."""
        if not self.user_uri or "users/me" in self.user_uri:
            logger.info("Calendly: Resolving 'users/me' to actual user URI...")
            resp = await self.client.get("https://api.calendly.com/users/me")
            if resp.status_code == 200:
                self.user_uri = resp.json().get("resource", {}).get("uri")
                logger.info(f"Calendly: Resolved user URI to {self.user_uri}")
                return self.user_uri
            else:
                logger.error(f"Calendly: Failed to resolve user URI: {resp.text}")
                raise ValueError(f"Could not resolve Calendly user URI: {resp.text}")
        return self.user_uri

    async def get_booking_url(self, event_type_slug: str = "30min") -> str | None:
        try:
            user_uri = await self._resolve_user_uri()
            # Corrected: Use user query parameter
            resp = await self.client.get("https://api.calendly.com/event_types", params={"user": user_uri})
            if resp.status_code == 200:
                data = resp.json()
                for event_type in data.get("collection", []):
                    if event_type.get("slug") == event_type_slug:
                        return event_type.get("scheduling_url")
        except Exception as e:
            logger.error(f"Error fetching booking URL: {e}")
        return None

    async def get_scheduled_events(self, since: Optional[datetime] = None) -> dict:
        """Fetch scheduled events from Calendly API (supports min_start_time)."""
        user_uri = await self._resolve_user_uri()
        url = "https://api.calendly.com/scheduled_events"
        params = {"user": user_uri, "status": "active"}
        if since:
            params["min_start_time"] = since.isoformat()
        
        logger.info(f"Calendly: Fetching events from {url} with params {params}")
        resp = await self.client.get(url, params=params)
        if resp.status_code == 200:
            return resp.json()
        else:
            logger.error(f"Calendly: Failed to fetch events ({resp.status_code}): {resp.text}")
            return {"error": resp.text}

    async def check_new_bookings(self, session, since: Optional[datetime] = None):
        """Check for new bookings by fetching events first, then their invitees."""
        events_data = await self.get_scheduled_events(since)
        new_bookings = []
        
        for event in events_data.get("collection", []):
            event_uri = event.get("uri")
            # Fetch invitees for this specific event
            invitees_resp = await self.client.get(f"{event_uri}/invitees")
            if invitees_resp.status_code != 200:
                logger.error(f"Calendly: Failed to fetch invitees for event {event_uri}")
                continue
                
            invitees_data = invitees_resp.json()
            for invitee in invitees_data.get("collection", []):
                raw_email = invitee.get("email")
                if raw_email:
                    email = raw_email.strip().lower()
                    # Find contact by email
                    from app.models.contact import Contact
                    from sqlmodel import select
                    contact = session.exec(select(Contact).where(Contact.email == email)).first()
                    if contact:
                        invitee_uri = invitee.get("uri")
                        ext = contact.external_ids if isinstance(contact.external_ids, dict) else {}
                        processed_uris = [b.get("invitee_uri") for b in ext.get("calendly", []) if isinstance(b, dict)]
                        
                        if invitee_uri not in processed_uris:
                            logger.info(f"Calendly: Found new booking for {email} (Invitee: {invitee_uri})")
                            new_bookings.append({
                                "contact": contact,
                                "event": event,
                                "metadata": {
                                    "invitee_email": email,
                                    "invitee_name": invitee.get("name"),
                                    "invitee_uri": invitee_uri,
                                    "event_uri": event_uri,
                                    "scheduled_start_time": event.get("start_time"),
                                    "event_type": "scheduled",
                                    "received_timestamp": datetime.utcnow().isoformat()
                                }
                            })
                        else:
                            logger.info(f"Calendly: Booking for {email} already processed (Invitee: {invitee_uri})")
        return new_bookings
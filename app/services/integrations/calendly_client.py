import httpx
from typing import Optional
from datetime import datetime
from sqlmodel import Session, select
from app.models.integration import Integration

class CalendlyClient:
    def __init__(self, session: Session):
        integration = session.exec(
            select(Integration).where(Integration.provider == "scheduling").where(Integration.status == "connected")
        ).first()
        if not integration:
            raise ValueError("Scheduling integration not found or not connected")
        config = integration.config_json or {}
        self.access_token = config.get("access_token")
        if not self.access_token:
            raise ValueError("Scheduling access token not configured")
        self.user_uri = config.get("user_uri", "https://api.calendly.com/users/me")
        self.client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=30.0
        )

    async def get_booking_url(self, event_type_slug: str = "30min") -> str | None:
        try:
            resp = await self.client.get(f"{self.user_uri}/event_types")
            if resp.status_code == 200:
                data = resp.json()
                for event_type in data.get("collection", []):
                    if event_type.get("slug") == event_type_slug:
                        return event_type.get("scheduling_url")
        except Exception as e:
            print(f"Error fetching booking URL: {e}")
        return None

    async def get_scheduled_events(self, since: Optional[datetime] = None) -> dict:
        """Fetch scheduled events from Calendly API."""
        url = "https://api.calendly.com/scheduled_events"
        params = {"user": self.user_uri}
        if since:
            params["min_start_time"] = since.isoformat()
        resp = await self.client.get(url, params=params)
        return resp.json() if resp.status_code == 200 else {"error": resp.text}

    async def check_new_bookings(self, session, since: Optional[datetime] = None):
        """Check for new bookings and return associated contacts."""
        events = await self.get_scheduled_events(since)
        new_bookings = []
        for event in events.get("collection", []):
            invitee_email = event.get("invitees", [{}])[0].get("email")
            if invitee_email:
                # Find contact by email (assuming session is SQLModel session)
                from app.models.contact import Contact
                from sqlmodel import select
                contact = session.exec(select(Contact).where(Contact.email == invitee_email)).first()
                if contact:
                    new_bookings.append({
                        "contact": contact,
                        "event": event,
                        "metadata": {
                            "invitee_email": invitee_email,
                            "invitee_uri": event.get("invitees", [{}])[0].get("uri"),
                            "event_uri": event.get("uri"),
                            "scheduled_start_time": event.get("start_time"),
                            "event_type": "scheduled",  # Since polling
                            "received_timestamp": datetime.utcnow().isoformat()
                        }
                    })
        return new_bookings
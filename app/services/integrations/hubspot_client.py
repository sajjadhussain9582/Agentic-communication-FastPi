import httpx
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional
from sqlmodel import Session, select
from app.core.config import settings
from app.models.integration import Integration

logger = logging.getLogger(__name__)

HUBSPOT_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
HUBSPOT_API_BASE = "https://api.hubapi.com"

class HubSpotClient:
    def __init__(self, session: Session):
        self.session = session
        self._integration: Optional[Integration] = None

    def _get_integration(self) -> Integration:
        if not self._integration:
            self._integration = self.session.exec(
                select(Integration).where(Integration.provider == "hubspot")
            ).first()
            if not self._integration:
                raise ValueError("HubSpot integration not found in database")
        return self._integration

    async def exchange_code_for_tokens(self, code: str) -> Dict[str, Any]:
        """Exchanges authorization code for access and refresh tokens."""
        data = {
            "grant_type": "authorization_code",
            "client_id": settings.HUBSPOT_CLIENT_ID,
            "client_secret": settings.HUBSPOT_CLIENT_SECRET,
            "redirect_uri": settings.HUBSPOT_REDIRECT_URI,
            "code": code,
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(HUBSPOT_TOKEN_URL, data=data)
            if response.status_code != 200:
                logger.error(f"HubSpot token exchange failed: {response.text}")
                raise Exception(f"Failed to exchange code: {response.text}")
            
            tokens = response.json()
            self._save_tokens(tokens)
            return tokens

    async def refresh_access_token(self) -> str:
        """Uses refresh token to get a new access token."""
        integration = self._get_integration()
        refresh_token = integration.config_json.get("refresh_token")
        if not refresh_token:
            raise ValueError("No refresh token available")

        data = {
            "grant_type": "refresh_token",
            "client_id": settings.HUBSPOT_CLIENT_ID,
            "client_secret": settings.HUBSPOT_CLIENT_SECRET,
            "refresh_token": refresh_token,
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(HUBSPOT_TOKEN_URL, data=data)
            if response.status_code != 200:
                logger.error(f"HubSpot token refresh failed: {response.text}")
                integration.status = "error"
                integration.last_error = "Token refresh failed"
                self.session.add(integration)
                self.session.commit()
                raise Exception("Failed to refresh HubSpot token")

            tokens = response.json()
            self._save_tokens(tokens)
            return tokens["access_token"]

    def _save_tokens(self, tokens: Dict[str, Any]):
        """Persists tokens to the Integration model."""
        integration = self._get_integration()
        
        # Calculate expiry
        expires_in = tokens.get("expires_in", 3600)
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
        
        config = integration.config_json or {}
        config.update({
            "access_token": tokens["access_token"],
            "refresh_token": tokens.get("refresh_token", config.get("refresh_token")),
            "expires_at": expires_at.isoformat(),
            "scopes": tokens.get("scope", "").split(" ")
        })
        
        integration.config_json = config
        integration.status = "connected"
        integration.last_sync_at = datetime.utcnow()
        integration.last_error = None
        self.session.add(integration)
        self.session.commit()
        self.session.refresh(integration)

    async def get_valid_access_token(self) -> str:
        """Returns a valid access token, refreshing it if necessary."""
        integration = self._get_integration()
        if not integration.config_json or "access_token" not in integration.config_json:
            raise ValueError("HubSpot not connected")

        expires_at_str = integration.config_json.get("expires_at")
        if expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str)
            # Refresh if expiring in less than 5 minutes
            if datetime.utcnow() + timedelta(minutes=5) > expires_at:
                return await self.refresh_access_token()

        return integration.config_json["access_token"]

    async def create_contact(self, properties: Dict[str, Any]) -> Dict[str, Any]:
        """Creates a contact in HubSpot."""
        token = await self.get_valid_access_token()
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts",
                headers={"Authorization": f"Bearer {token}"},
                json={"properties": properties}
            )
            if response.status_code not in (200, 201):
                logger.error(f"Failed to create HubSpot contact: {response.text}")
                return {"ok": False, "error": response.text}
            return {"ok": True, "data": response.json()}

    async def upsert_contact(self, email: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        """Creates or updates a contact in HubSpot using email as the unique identifier."""
        token = await self.get_valid_access_token()
        
        # HubSpot's batch upsert endpoint is often the most robust way to do this
        # but for a single contact, we can also use the search + update/create pattern
        # or the specific 'upsert' behavior if available in newer v3 endpoints.
        # Here we use the Batch Upsert pattern for robustness.
        
        payload = {
            "inputs": [
                {
                    "idProperty": "email",
                    "id": email,
                    "properties": properties
                }
            ]
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{HUBSPOT_API_BASE}/crm/v3/objects/contacts/batch/upsert",
                headers={"Authorization": f"Bearer {token}"},
                json=payload
            )
            
            if response.status_code not in (200, 201):
                logger.error(f"HubSpot upsert failed: {response.text}")
                return {"ok": False, "error": response.text}
            
            return {"ok": True, "data": response.json()}

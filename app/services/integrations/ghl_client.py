import logging
import httpx
from typing import Any
from sqlmodel import Session, select
from app.models.contact import Contact
from app.models.integration import Integration

logger = logging.getLogger(__name__)

def sync_contact_to_ghl(session: Session, contact: Contact) -> bool:
    """Sync contact state to GoHighLevel if integration is enabled."""
    integration = session.exec(select(Integration).where(Integration.provider == "ghl", Integration.status == "connected")).first()
    if not integration or not integration.config_json:
        return False
        
    token = integration.config_json.get("access_token")
    if not token:
        logger.warning("GHL integration is connected but missing access_token.")
        return False
        
    ext = contact.external_ids if isinstance(contact.external_ids, dict) else {}
    ghl_contact_id = ext.get("ghl_contact_id")
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Version": "2021-07-28",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    payload = {}
    if contact.email:
        payload["email"] = contact.email
    if contact.phone:
        payload["phone"] = contact.phone
    if contact.username:
        payload["name"] = contact.username
    if contact.company:
        payload["companyName"] = contact.company
        
    if contact.pipeline_stage:
        payload["tags"] = [contact.pipeline_stage.lower()]
        
    location_id = integration.config_json.get("location_id")
    if location_id:
        payload["locationId"] = location_id
        
    url = "https://services.leadconnectorhq.com/contacts/"
    if ghl_contact_id:
        url += f"{ghl_contact_id}"
        method = "PUT"
    else:
        method = "POST"
        
    with httpx.Client() as client:
        try:
            res = client.request(method, url, headers=headers, json=payload, timeout=5.0)
            res.raise_for_status()
            data = res.json()
            if not ghl_contact_id and "contact" in data:
                ext["ghl_contact_id"] = data["contact"].get("id")
                contact.external_ids = ext
            return True
        except Exception as e:
            logger.error(f"Failed to sync contact {contact.id} to GHL: {e}")
            return False

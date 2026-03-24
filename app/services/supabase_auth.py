from typing import Any, Dict, Optional

import httpx
from fastapi import HTTPException
from pydantic import BaseModel

from app.core.config import settings


class SupabaseTokenRequest(BaseModel):
    access_token: str


class SupabaseUser(BaseModel):
    id: str
    email: str
    name: Optional[str] = None
    raw: Dict[str, Any]


async def fetch_supabase_user(access_token: str) -> SupabaseUser:
    url = f"{settings.SUPABASE_URL}/auth/v1/user"

    headers = {
        "Authorization": f"Bearer {access_token}",
        "apikey": settings.SUPABASE_ANON_KEY,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="Failed to reach Supabase Auth service")

    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired Supabase access token")

    data = response.json()

    email = data.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Supabase user payload missing email")

    user_id = data.get("id") or data.get("sub")

    user_metadata = data.get("user_metadata") or {}
    name = user_metadata.get("name") or user_metadata.get("full_name")

    return SupabaseUser(id=str(user_id) if user_id else email, email=email, name=name, raw=data)


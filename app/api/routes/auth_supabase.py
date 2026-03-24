from fastapi import APIRouter, Response

from app.core.security import create_session_token
from app.services.supabase_auth import SupabaseTokenRequest, fetch_supabase_user
from app.services.user_service import get_or_create_user_from_supabase


router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/supabase/login")
async def supabase_login(payload: SupabaseTokenRequest, response: Response):
    supabase_user = await fetch_supabase_user(payload.access_token)

    user = get_or_create_user_from_supabase(
        email=supabase_user.email,
        supabase_id=supabase_user.id,
        name=supabase_user.name,
    )

    token = create_session_token(user.id)

    response.set_cookie(
        key="session_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
    )

    return {
        "access_token": token,
        "token_type": "bearer",
    }


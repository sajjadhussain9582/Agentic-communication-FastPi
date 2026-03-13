from passlib.context import CryptContext
from itsdangerous import URLSafeTimedSerializer
from app.core.config import settings
from fastapi import HTTPException

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
serializer = URLSafeTimedSerializer(settings.SECRET_KEY)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def create_session_token(user_id: int) -> str:
    return serializer.dumps({"uid": user_id})


def verify_session_token(token: str):
    return serializer.loads(token, max_age=settings.SESSION_MAX_AGE)

def verify_token(token:str):
    try:
        return serializer.loads(token, max_age=settings.SESSION_MAX_AGE)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    
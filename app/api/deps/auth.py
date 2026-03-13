from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session, select
from app.db.session import engine
from app.models.user import User
from app.core.security import verify_session_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/users/login")


def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    try:
        data = verify_session_token(token)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    with Session(engine) as session:
        user = session.exec(select(User).where(User.id == data["uid"])).first()

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    return user
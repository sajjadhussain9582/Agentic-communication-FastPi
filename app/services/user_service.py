from typing import Optional

from sqlmodel import Session, select

from app.db.session import engine
from app.models.user import User


def get_or_create_user_from_supabase(
    email: str,
    supabase_id: str,
    name: Optional[str] = None,
    status: str = "active",
) -> User:
    with Session(engine) as session:
        user = session.exec(
            select(User).where(User.supabase_id == supabase_id)
        ).first()

        if not user:
            user = session.exec(
                select(User).where(User.email == email)
            ).first()

        if not user:
            user = User(
                email=email,
                password=None,
                status=status,
                supabase_id=supabase_id,
            )
            session.add(user)
        else:
            if not user.supabase_id:
                user.supabase_id = supabase_id

        session.commit()
        session.refresh(user)

        return user


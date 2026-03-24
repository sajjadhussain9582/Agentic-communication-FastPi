from fastapi import APIRouter, Response, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from typing import List
from app.db.session import engine
from app.schemas.user import UserCreate, UserLogin
from app.models.user import User
from app.core.security import hash_password, verify_password, create_session_token
from app.api.deps.auth import get_current_user

router = APIRouter(tags=["Users"])


@router.post("/register")
def register(user_in: UserCreate):
    if not user_in.password:
        raise HTTPException(status_code=400, detail="Password required")
    with Session(engine) as session:
        user = User(
            email=user_in.email,
            password=hash_password(user_in.password),
            status=user_in.status if user_in.status else "active",
        )
        session.add(user)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(status_code=409, detail="Email already registered")
        session.refresh(user)
    return {"message": "User created"}


@router.post("/login")
def login(user_in: UserLogin, response: Response):
    with Session(engine) as session:
        user = session.exec(
            select(User).where(User.email == user_in.email)
        ).first()

        if not user or not verify_password(user_in.password, user.password):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        token = create_session_token(user.id)

        response.set_cookie(
            key="session_token", 
            value=token, 
            httponly=True,   # Essential for security
            secure=True,     # Only send over HTTPS
            samesite="lax"   # Helps prevent CSRF
        )
        
    return {
        "access_token": token,
        "token_type": "bearer"
    }


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/all", response_model=List[User])
def get_all_users(current_user: User = Depends(get_current_user)):  
    with Session(engine) as session:
        users = session.exec(select(User)).all()
    return users

@router.put("/{id}")
def update_user(id:int, user_in:UserCreate, current_user:User=Depends(get_current_user)):
    with Session(engine) as session:
        user=session.exec(select(User).where(User.id==id)).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        user.email=user_in.email
        user.password=hash_password(user_in.password)
        user.status=user_in.status
        session.add(user)
        session.commit()
        session.refresh(user)
    return user
    
@router.delete("/{id}")
def delete_user(id:int, current_user:User=Depends(get_current_user)):
    with Session(engine) as session:
        user=session.exec(select(User).where(User.id==id)).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        session.delete(user)
        session.commit()
    return {"message": "User deleted"}
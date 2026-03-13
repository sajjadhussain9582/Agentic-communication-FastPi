from pydantic import BaseModel, EmailStr
from typing import Optional

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    status: Optional[str] = "active"

class UserLogin(BaseModel):
    email: EmailStr
    password: str

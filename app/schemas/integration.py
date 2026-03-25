from typing import Optional, Literal
from pydantic import BaseModel, EmailStr

class EmailConfigSMTP(BaseModel):
    type: Literal["smtp"]
    host: str
    port: int
    user: str
    password: str
    use_tls: bool = True
    from_email: EmailStr

class EmailConfigResend(BaseModel):
    type: Literal["resend"]
    api_key: str
    from_email: EmailStr

class CalendlyConfig(BaseModel):
    type: Literal["pat", "url"]
    token: Optional[str] = None
    booking_url: Optional[str] = None
    webhook_url: Optional[str] = None

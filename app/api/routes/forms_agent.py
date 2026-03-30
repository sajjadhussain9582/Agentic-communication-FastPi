from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlmodel import Session, select

from app.api.deps.auth import get_current_user
from app.core.config import settings
from app.core.security import verify_session_token
from app.db.session import get_session
from app.models.form import Form
from app.models.user import User
from app.schemas.forms_agent import FormCreate, FormRead
from app.services.form_intake import process_form_submission

router = APIRouter(prefix="/forms", tags=["Forms & AI intake"])


def _optional_submit_auth(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        try:
            verify_session_token(token)
            return
        except Exception:
            pass
    if settings.FORM_SUBMIT_API_KEY and x_api_key != settings.FORM_SUBMIT_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


@router.post("", response_model=FormRead)
def create_form(
    body: FormCreate,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    f = Form(title=body.title, description=body.description, json_config=body.json_config)
    session.add(f)
    session.commit()
    session.refresh(f)
    return FormRead(
        id=f.id,
        uuid=f.public_uuid,
        title=f.title,
        description=f.description,
        json_config=f.json_config or {},
    )


@router.get("", response_model=list[FormRead])
def list_forms(
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return [
        FormRead(
            id=f.id,
            uuid=f.public_uuid,
            title=f.title,
            description=f.description,
            json_config=f.json_config or {},
        )
        for f in session.exec(select(Form)).all()
    ]


@router.get("/{form_id}", response_model=FormRead)
def get_form(
    form_id: int,
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    f = session.get(Form, form_id)
    if not f:
        raise HTTPException(404, "Form not found")
    return FormRead(
        id=f.id,
        uuid=f.public_uuid,
        title=f.title,
        description=f.description,
        json_config=f.json_config or {},
    )


@router.post("/{form_id}/submit")
async def submit_form(
    form_id: int,
    body: dict[str, Any],
    session: Session = Depends(get_session),
    _auth: None = Depends(_optional_submit_auth),
):
    result = await process_form_submission(session, form_id, body)
    if result.get("error") == "form_not_found":
        raise HTTPException(404, "Form not found")
    return result

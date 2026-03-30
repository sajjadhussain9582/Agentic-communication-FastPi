from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from typing import List
from app.api.deps.auth import get_current_user
from app.db.session import get_session
from app.models.pipeline_stage import PipelineStage
from app.models.user import User
from pydantic import BaseModel

router = APIRouter(prefix="/pipeline-stages", tags=["Pipeline Stages"])

class PipelineStageRead(BaseModel):
    id: int
    key: str
    pipelinestage: str
    order_index: int
    ai_instructions: str | None

@router.get("", response_model=List[PipelineStageRead])
def list_pipeline_stages(
    _: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    stages = session.exec(select(PipelineStage).order_by(PipelineStage.order_index)).all()
    return stages

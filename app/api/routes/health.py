from fastapi import APIRouter
from datetime import datetime

router = APIRouter()


@router.get("/health", tags=["Health"])
async def healthcheck():
    return {
        "status": "ok",
        "service": "running",
        "timestamp": datetime.utcnow().isoformat()
    }
@router.get("/check", tags=["check"])
async def checking():
    return {
        "status":"ok",
        "service":"running",
        "timestamp":datetime.utcnow().isoformat()
    }
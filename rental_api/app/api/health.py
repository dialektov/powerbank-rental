from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.db import Database
from app.dependencies import get_db

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready(db: Annotated[Database, Depends(get_db)]) -> JSONResponse:
    try:
        await db.main.fetchval("SELECT 1")
    except Exception:
        return JSONResponse(status_code=503, content={"status": "db_unavailable"})
    return JSONResponse(content={"status": "ok"})

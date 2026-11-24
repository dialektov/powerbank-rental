from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.dependencies import Services

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready(services: Services) -> JSONResponse:
    try:
        await services.db.main.fetchval("SELECT 1")
    except Exception:
        return JSONResponse(status_code=503, content={"status": "db_unavailable"})
    return JSONResponse(content={"status": "ok"})

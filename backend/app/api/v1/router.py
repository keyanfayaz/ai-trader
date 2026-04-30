from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints.agents import router as agents_router
from app.api.v1.endpoints.research import router as research_router
from app.api.v1.endpoints.settings import router as settings_router
from app.api.v1.endpoints.trading import router as trading_router


router = APIRouter()
router.include_router(agents_router, prefix="/agents", tags=["agents"])
router.include_router(trading_router, prefix="/trading", tags=["trading"])
router.include_router(research_router, prefix="/research", tags=["research"])
router.include_router(settings_router, prefix="/settings", tags=["settings"])

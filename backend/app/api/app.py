from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.dependencies import get_agent_management_service, get_agent_scheduler, get_settings
from app.api.v1.router import router as v1_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure env validation + logging setup happen at application boot.
    _ = get_settings()
    agent_service = get_agent_management_service()
    agent_service.pause_active_agents_on_startup()
    scheduler = get_agent_scheduler()
    scheduler.start()
    yield
    scheduler.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Auto Trader API",
        version="1.0.0",
        description="API layer for trading actions and AI-assisted market research.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(v1_router, prefix="/api/v1")

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app

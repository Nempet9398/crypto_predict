import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.core.migrations import run_migrations
from app.routers import data, health, prices
from app.routers import features as features_router
from app.routers import signals

logger = logging.getLogger(__name__)


def create_app():
    app = FastAPI(
        title="ETH Trading Dashboard API",
        description="이더리움 트레이딩 대시보드 백엔드",
        version="2.0.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(prices.router)
    app.include_router(data.router)
    app.include_router(features_router.router)
    app.include_router(signals.router)

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse(url="/docs")

    @app.on_event("startup")
    def startup_event():
        run_migrations()
        logger.info("ETH Trading Dashboard API v2.0 started.")

    return app


app = create_app()

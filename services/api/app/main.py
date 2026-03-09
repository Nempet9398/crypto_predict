import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.core.data_sync import get_data_status
from app.routers import data, health, model, prices

logger = logging.getLogger(__name__)


def create_app():
    app = FastAPI(title="Ethereum Data API")
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
    app.include_router(model.router)

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse(url="/docs")

    @app.on_event("startup")
    def startup_event():
        try:
            status = get_data_status()
            logger.info("startup data status: rows=%s gaps=%s max_ts=%s", status["row_count"], status["gap_count"], status["max_ts"])
        except Exception as exc:
            logger.warning("startup data status check failed: %s", exc)

    return app


app = create_app()

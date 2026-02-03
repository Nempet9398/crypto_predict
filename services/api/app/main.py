from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.model import load_model
from app.routers import features, health, predict, prices


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
    app.include_router(features.router)
    app.include_router(predict.router)

    @app.on_event("startup")
    def startup_event():
        app.state.model = load_model()

    return app


app = create_app()

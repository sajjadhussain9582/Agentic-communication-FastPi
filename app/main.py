from fastapi import FastAPI
from app.api.router import api_router
from app.core.middleware import setup_cors
from app.core.logging import setup_logging
from app.core.database import init_db


def create_app():
    app = FastAPI()

    setup_logging()
    setup_cors(app)

    @app.on_event("startup")
    def on_startup():
        init_db()

    app.include_router(api_router, prefix="/api/v1")

    return app


app = create_app()
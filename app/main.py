from fastapi import FastAPI
from app.api.router import api_router
from app.api.routes.users import router as users_router
from app.core.middleware import setup_cors
from app.core.logging import setup_logging
from app.core.database import init_db
from app.core.config import settings


def create_app():
    app = FastAPI()

    setup_logging()
    setup_cors(app)

    @app.on_event("startup")
    def on_startup():
        init_db()
        from app.services.ai_graph import setup_checkpointer
        setup_checkpointer()
        
        from sqlmodel import Session

        from app.core.database import engine
        from app.core.seed import seed_if_empty

        with Session(engine) as session:
            seed_if_empty(session)
        
        # Initialize LangSmith tracing (optional, graceful failure)
        from app.core.langsmith_setup import initialize_langsmith
        initialize_langsmith(
            api_key=settings.LANGSMITH_API_KEY,
            tracing_enabled=settings.LANGSMITH_TRACING,
            project_name=settings.LANGSMITH_PROJECT,
            endpoint=settings.LANGSMITH_ENDPOINT if settings.LANGSMITH_ENDPOINT else None,
        )
        
        from app.services.worker import start_worker
        start_worker()

    # Main API under /api/v1 (e.g. /api/v1/users/register)
    app.include_router(api_router, prefix="/api/v1")
    # Frontend-friendly path: /v1/user/register (singular, no "api")
    app.include_router(users_router, prefix="/v1/user")

    return app


app = create_app()
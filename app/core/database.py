from sqlmodel import SQLModel, Session, create_engine
from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    echo=False,  # Set True for SQL debugging
    pool_pre_ping=True,
    connect_args={"prepare_threshold": None} if "psycopg" in settings.DATABASE_URL or True else {}
)


def init_db():
    import app.models  # noqa: F401 — register all SQLModel tables
    SQLModel.metadata.create_all(engine)
    from app.core.phase2_migrate import (
        backfill_uuids,
        ensure_default_integrations,
        ensure_schema_phase2,
    )

    ensure_schema_phase2()
    with Session(engine) as session:
        backfill_uuids(session)
        ensure_default_integrations(session)

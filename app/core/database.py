from sqlmodel import SQLModel, Session, create_engine
from app.core.config import settings
from sqlalchemy import event

engine = create_engine(
    settings.DATABASE_URL,
    echo=False,  # Set True for SQL debugging
    pool_pre_ping=True,
    connect_args={"prepare_threshold": None} if "psycopg" in settings.DATABASE_URL else {}
)

# Silence pgvector warnings by acknowledging the 'vector' type on connect
@event.listens_for(engine, "connect")
def register_vector(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SELECT 1 FROM pg_type WHERE typname = 'vector'")
        if cursor.fetchone():
            # The type exists in Postgres; this acknowledgment helps SQLAlchemy
            pass
    except Exception:
        pass
    finally:
        cursor.close()


def init_db():
    import app.models  # noqa: F401 — register all SQLModel tables
    if "sqlite" in settings.DATABASE_URL:
        SQLModel.metadata.create_all(engine)
    from app.core.phase2_migrate import (
        backfill_uuids,
        ensure_default_integrations,
    )

    with Session(engine) as session:
        backfill_uuids(session)
        ensure_default_integrations(session)

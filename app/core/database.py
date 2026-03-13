from sqlmodel import SQLModel, create_engine
from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    echo=False,  # Set True for SQL debugging
)


def init_db():
    SQLModel.metadata.create_all(engine)
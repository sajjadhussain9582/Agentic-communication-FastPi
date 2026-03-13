from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "Agentic Communication"
    APP_VERSION: str = "1.0.0"
    DATABASE_URL: str
    SECRET_KEY: str = "supersecretkey"
    SESSION_MAX_AGE: int = 3600
    REDIS_HOST: str = "localhost"

    class Config:
        env_file = ".env"


settings = Settings()
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "Agentic Communication"
    APP_VERSION: str = "1.0.0"
    DATABASE_URL: str
    SECRET_KEY: str = "supersecretkey"
    SESSION_MAX_AGE: int = 3600
    REDIS_HOST: str = "localhost"
    SUPABASE_URL: str
    SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_ANON_KEY: str

    OPENAI_API_KEY: str = ""
    OPENAI_CHAT_MODEL: str = "gpt-4o-mini"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    CALENDLY_BOOKING_URL: str = ""
    FORM_SUBMIT_API_KEY: str = ""  # If set, require X-API-Key header on public submit
    PUBLIC_APP_URL: str = ""  # Used in integration OAuth stub URLs
    GHL_WEBHOOK_SECRET: str = ""  # Optional: require X-GHL-Secret or Authorization
    AGENT_NAME: str = "Partnership Team"

    # Gmail/SMTP Settings
    GMAIL_USER: str = ""
    GMAIL_APP_PASSWORD: str = ""
    SMTP_PORT: int = 587

    class Config:
        env_file = ".env"
        extra = "allow"


settings = Settings()
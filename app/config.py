"""Central configuration, read from environment variables or a .env file."""
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./invoice_chaser.db"

    secret_key: str = "dev-only-secret-change-me-in-production-0123456789"
    access_token_minutes: int = 720

    llm_provider: str = "template"  # template | gemini | groq
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    whatsapp_mode: str = "mock"  # mock | cloud
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = "change-me-verify-token"
    whatsapp_app_secret: str = ""
    whatsapp_template_name: str = "invoice_reminder"
    whatsapp_template_lang: str = "en"

    timezone: str = "Asia/Kolkata"
    quiet_start_hour: int = 21
    quiet_end_hour: int = 9
    max_reminders: int = 5
    min_gap_days: int = 2
    max_messages_per_client_per_week: int = 3
    require_approval: bool = False

    scheduler_enabled: bool = True
    scheduler_hours: str = "10,16"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def now_local() -> datetime:
    """Current time in the business time zone (aware datetime)."""
    return datetime.now(ZoneInfo(settings.timezone))

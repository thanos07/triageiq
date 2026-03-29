"""
app/config.py

Central configuration for the Incident Triage Copilot.
All settings are read from environment variables (or .env file).
Import the `settings` singleton anywhere you need config — never read
os.environ directly in other modules.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    """
    Application-wide settings.
    Values are loaded from the .env file (or real environment variables).
    Pydantic-settings handles type coercion and validation automatically.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # silently ignore unknown env vars
    )

    # ── Application ───────────────────────────────────────────────────────────
    app_name: str = "Enterprise Incident Triage Copilot"
    app_env: str = "development"
    log_level: str = "INFO"

    # ── API Server ────────────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "sqlite:///./data/triage.db"

# ── LLM Provider ─────────────────────────────────────────────────────────
    anthropic_api_key: str = ""
    llm_model: str = "claude-3-5-haiku-20241022"
    llm_max_tokens: int = 1024
    llm_timeout_seconds: int = 30
    llm_max_retries: int = 3

    # ── Groq (Free alternative) ───────────────────────────────────────────────
    groq_api_key: str = ""
    groq_model: str = "llama-3.1-8b-instant"

    # ── Governance ────────────────────────────────────────────────────────────
    low_confidence_threshold: float = 0.50
    min_confidence_score: float = 0.10
    max_confidence_score: float = 0.95

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() == "development"


@lru_cache()
def get_settings() -> Settings:
    """
    Return a cached Settings singleton.
    Using lru_cache means .env is only read once at startup.
    """
    return Settings()


# Module-level singleton for convenience:  from app.config import settings
settings = get_settings()

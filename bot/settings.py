from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_ALLOWED_USER_ID: int

    FINTRACKER_API_URL: str
    FINTRACKER_USERNAME: str
    FINTRACKER_PASSWORD: str

    ANTHROPIC_API_KEY: str
    ANTHROPIC_MODEL: str = "claude-haiku-5-5"

    TIMEZONE: str = "America/Sao_Paulo"

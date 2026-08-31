"""Runtime configuration.

Confidence thresholds are configuration rather than constants because the
correct threshold for handwritten Devanagari differs from printed English
lab tables. See solution document section 14.4.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEDIKIOSK_", env_file=".env",
                                      extra="ignore")

    app_name: str = "MediKiosk"
    environment: str = "development"

    database_url: str = (
        "postgresql+psycopg://medikiosk:medikiosk@localhost:5432/medikiosk"
    )

    jwt_secret: str = "dev-only-change-me"
    jwt_algorithm: str = "HS256"
    patient_token_minutes: int = 20
    staff_token_minutes: int = 30

    conf_high: float = 0.85
    conf_medium: float = 0.60


    conf_high_document: float = 0.90
    conf_medium_document: float = 0.70

    conf_unreadable_document: float = 0.35

    default_red_flag_sla_seconds: int = 300


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

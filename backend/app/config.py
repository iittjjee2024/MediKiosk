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

    # --- confidence gate bands (see engines/confidence.py) -------------------
    # >= high            -> admitted as a Clinical Fact
    # >= medium, < high  -> admitted, marked unconfirmed, confirm-back asked
    # <  medium          -> NOT admitted; routed to human verification
    conf_high: float = 0.85
    conf_medium: float = 0.60
    # documents are held to a higher bar than speech: a misread drug name is
    # more dangerous than a misheard symptom the patient can re-confirm
    conf_high_document: float = 0.90
    conf_medium_document: float = 0.70
    # below this an OCR region is declared unreadable and shown as an image
    conf_unreadable_document: float = 0.35

    default_red_flag_sla_seconds: int = 300


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

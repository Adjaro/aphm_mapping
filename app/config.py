"""Configuration de l'application (variables d'environnement / fichier .env)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_NAME = "Référentiel mappings OMOP — AP-HM"
BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://omop:omop@localhost:5432/omop_referentiel"
    test_database_url: str | None = None
    upload_dir: Path = BASE_DIR / "var" / "uploads"
    log_level: str = "INFO"
    import_chunk_size: int = 50_000
    max_upload_mb: int = 200


@lru_cache
def get_settings() -> Settings:
    return Settings()

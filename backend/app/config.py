"""Настройки приложения. Все значения приходят из .env (раздел 3.2 ТЗ).

Ни одного секрета в коде — правило 10.1: секрет в git это инцидент.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- база ---
    DATABASE_URL: str = "postgresql+asyncpg://soro:soro@db:5432/soro"
    REDIS_URL: str = "redis://redis:6379/0"

    # --- Soro LLM ---
    SORO_API_URL: str = "http://10.0.0.5:8000/v1"
    SORO_API_KEY: str = "dev-key-change-me"
    SORO_MODEL: str = "soro-27b-fp8"
    SORO_MAX_TOKENS: int = 700
    SORO_TEMPERATURE: float = 0.2

    # --- эмбеддинги ---
    EMBEDDINGS_URL: str = "http://embeddings:80"
    EMBEDDINGS_DIM: int = 1024  # у bge-m3 размерность 1024, НЕ менять

    # --- RAG ---
    RAG_TOP_K: int = 12  # сколько кандидатов достаём из БД
    RAG_RETURN_K: int = 3  # сколько фрагментов кладём в промпт
    RAG_MIN_SCORE: float = 0.65  # порог: ниже — считаем, что ответа нет

    # --- каналы ---
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_WEBHOOK_SECRET: str = ""
    WHATSAPP_TOKEN: str = ""
    WHATSAPP_PHONE_ID: str = ""
    WHATSAPP_VERIFY_TOKEN: str = ""

    # --- прочее ---
    PUBLIC_BASE_URL: str = "http://localhost:8000"
    CONSOLE_LOGIN: str = "admin"
    CONSOLE_PASSWORD: str = ""
    WORKSPACE_DEFAULT_SLUG: str = "eskhata-demo"

    # --- константы, не выносимые в .env ---
    UPLOAD_DIR: str = "/data/uploads"
    # регэксп триггера user_request из раздела 8.1
    OPERATOR_REQUEST_RE: str = r"\b(оператор|одам|человек|мутахассис)\b"


settings = Settings()

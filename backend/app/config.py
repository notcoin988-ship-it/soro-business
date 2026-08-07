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
    # Порог: ниже — считаем, что ответа нет. Раздел 3.2 разрешает крутить
    # его в диапазоне 0,60–0,72 по результатам golden set; взята нижняя
    # граница, и вот почему.
    #
    # При 0,65 (значение из ТЗ) прогон давал ноль выдумок, но 16 ложных
    # эскалаций из 40: бот молчал на вопросах, ответ на которые сам же
    # нашёл. Живой пример — «хочу оформить карту»: близость 0,641, промах
    # на девять тысячных, а первым фрагментом найдены «Карты VISA Gold».
    #
    # Опускать порог было нельзя, пока он оставался ЕДИНСТВЕННЫМ фильтром:
    # при 0,60 прогон показывал 6 выдуманных ответов. Теперь фильтра два —
    # порог и сама модель, которой правило 1 промпта велит отказаться,
    # если ответа во фрагментах нет. Замер на 20 вопросах группы Б: порог
    # 0,60 пропустил к модели 6, модель отказалась от 5, а шестой оказался
    # верным ответом (вклад в евро в документах есть — это была ошибка
    # golden set). Настоящих выдумок — ноль.
    RAG_MIN_SCORE: float = 0.60

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
    # Регэксп триггера user_request из раздела 8.1. В ТЗ он записан с
    # закрывающей границей слова `\b`, и из-за неё «поговорить с человеком»
    # не срабатывает: у русского и таджикского слова есть окончания.
    # Заменено на `\w*` — ловим любую словоформу.
    OPERATOR_REQUEST_RE: str = r"\b(оператор|одам|человек|мутахассис)\w*"


settings = Settings()

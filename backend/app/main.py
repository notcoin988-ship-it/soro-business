"""Создание FastAPI-приложения (раздел 2.3 ТЗ).

Здесь собирается приложение: /health и роутеры каналов. Логики нет и не
должно быть — она в core/.
"""

from fastapi import FastAPI

from app.api import console, inbox, playground
from app.channels import telegram
from app.config import settings

app = FastAPI(title="Soro Business Console", version="1.0.0")

app.include_router(console.router)
app.include_router(playground.router)
app.include_router(inbox.router)
# Каналы подключаются по мере готовности. Telegram первый — раздел 7.1.
app.include_router(telegram.router)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "workspace": settings.WORKSPACE_DEFAULT_SLUG,
        "model": settings.SORO_MODEL,
    }

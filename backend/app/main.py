"""Создание FastAPI-приложения (раздел 2.3 ТЗ).

Пока здесь только каркас и /health — роутеры разделов 7–9 подключаются по мере
готовности. Файл нужен, чтобы контейнер backend поднимался и проходила
проверка №1 чек-листа 3.4.
"""

from fastapi import FastAPI

from app.config import settings

app = FastAPI(title="Soro Business Console", version="1.0.0")


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "workspace": settings.WORKSPACE_DEFAULT_SLUG,
        "model": settings.SORO_MODEL,
    }

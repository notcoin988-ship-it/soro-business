"""Проверка здоровья: что именно живо, а что нет.

ЗАЧЕМ ГЛУБОКАЯ. `/health` отвечал «ok» ровно потому, что процесс жив, —
и продолжал бы отвечать «ok» с мёртвой базой, отвалившимся Redis и
недоступной моделью. На демо это выглядело так: консоль открывается,
цифры не грузятся, никто не понимает, что сломалось.

  GET /health        как было: жив ли процесс. Для балансировщика.
  GET /health/ready  что живо на самом деле: база, Redis, эмбеддинги,
                     модель, вебхук Telegram.

РАЗНЫЕ АДРЕСА НАМЕРЕННО. Балансировщику нужен быстрый ответ без походов
в чужие сервисы, иначе медленная модель начнёт выбивать здоровый
бэкенд из ротации. Глубокая проверка нужна человеку и сторожу
(`scripts/watchdog.py`) — им лишние полсекунды не жалко.

КОД ОТВЕТА. 200, если работает то, без чего бот не отвечает вообще:
база, Redis, эмбеддинги. Недоступная модель или неприписанный вебхук —
это `degraded` и всё равно 200: бот в этом состоянии эскалирует к
оператору, то есть работает, просто хуже.
"""

from __future__ import annotations

import asyncio
import time

import httpx
from fastapi import APIRouter
from redis.asyncio import Redis
from sqlalchemy import text

from app.config import is_filled, settings
from app.db import SessionLocal

router = APIRouter(tags=["health"])

# Столько ждём каждый сервис. Две секунды — это уже «не работает»: у нас
# норматив шесть секунд на весь ответ клиенту.
TIMEOUT_SEC = 2.0

# Без чего бот не отвечает совсем. Модель и Telegram сюда не входят
# намеренно — см. шапку.
CRITICAL = ("database", "redis", "embeddings")


async def _check(name: str, probe) -> tuple[str, dict]:
    started = time.monotonic()
    try:
        await asyncio.wait_for(probe(), timeout=TIMEOUT_SEC)
        state, detail = "ok", ""
    except asyncio.TimeoutError:
        state, detail = "fail", f"не ответил за {TIMEOUT_SEC} с"
    except Exception as exc:  # noqa: BLE001 — проверка не должна падать
        state, detail = "fail", f"{type(exc).__name__}: {exc}"
    return name, {
        "state": state,
        "detail": detail,
        "ms": int((time.monotonic() - started) * 1000),
    }


async def _database() -> None:
    async with SessionLocal() as session:
        await session.execute(text("SELECT 1"))


async def _redis() -> None:
    client = Redis.from_url(settings.REDIS_URL)
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _embeddings() -> None:
    async with httpx.AsyncClient(timeout=TIMEOUT_SEC) as client:
        response = await client.post(
            f"{settings.EMBEDDINGS_URL}/embed", json={"inputs": "проверка"}
        )
        response.raise_for_status()


async def _model() -> None:
    async with httpx.AsyncClient(timeout=TIMEOUT_SEC) as client:
        response = await client.get(
            f"{settings.SORO_API_URL}/models",
            headers={"Authorization": f"Bearer {settings.SORO_API_KEY}"},
        )
        response.raise_for_status()


async def _telegram() -> None:
    """Прописан ли вебхук и не копит ли Telegram ошибки доставки.

    Самая частая поломка демо: ngrok сменил адрес, бот замолчал, и
    узнать об этом можно было только открыв экран 05.
    """
    if not is_filled(settings.TELEGRAM_BOT_TOKEN):
        raise RuntimeError("токен бота не задан")

    from app.channels.telegram import get_bot

    info = await get_bot().get_webhook_info()
    if not info.url:
        raise RuntimeError("вебхук не прописан")
    if info.last_error_message:
        raise RuntimeError(f"Telegram не доставляет: {info.last_error_message}")


@router.get("/health")
async def health() -> dict:
    """Жив ли процесс. Быстро и без походов наружу."""
    return {
        "status": "ok",
        "workspace": settings.WORKSPACE_DEFAULT_SLUG,
        "model": settings.SORO_MODEL,
    }


@router.get("/health/ready")
async def ready() -> dict:
    """Что живо на самом деле."""
    checks = dict(
        await asyncio.gather(
            _check("database", _database),
            _check("redis", _redis),
            _check("embeddings", _embeddings),
            _check("model", _model),
            _check("telegram", _telegram),
        )
    )

    broken = [name for name, result in checks.items() if result["state"] == "fail"]
    critical = [name for name in broken if name in CRITICAL]

    return {
        "status": "fail" if critical else ("degraded" if broken else "ok"),
        # Списком, а не по одному ключу: человеку в логе сторожа нужно
        # сразу видеть, что именно чинить.
        "broken": broken,
        "checks": checks,
    }

"""Каналы — экран 05.

ОТВЕТСТВЕННОСТЬ: состояние каждого канала прямо сейчас и всё, что нужно,
чтобы его подключить или починить.

  GET /api/channels

ЗАЧЕМ ЭТО ЖИВОЕ. В прототипе на карточках зашиты плашки «Активен», и
экран красив ровно до того момента, когда ngrok сменил адрес и Telegram
замолчал: плашка всё равно горит зелёным. Перед каждой встречей канал
проверяют глазами — пусть проверяет экран.

  * Telegram — есть ли токен и куда прописан вебхук; сколько апдейтов
    Telegram не смог доставить (это и есть «бот молчит»);
  * виджет — готов всегда, но выдавать банку нечего, пока нет публичного
    адреса: сниппет со строкой «ЗАПОЛНИТЬ-после-ngrok» вставить некуда;
  * WhatsApp — токен песочницы живёт 24 часа, и «подключён» здесь значит
    ровно «в .env лежит непустой токен», а не «работает».

ПОХОД В TELEGRAM — ЛУЧШЕЕ УСИЛИЕ. `getWebhookInfo` идёт по сети, и если
интернета нет, экран должен показать «не смогли спросить», а не висеть
или падать: остальные две карточки от этого не зависят.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.analytics import CHANNELS_SQL
from app.config import is_filled, settings
from app.core.dialog import get_workspace
from app.db import get_session

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["channels"])

# Окно счётчика на карточках — то же, что на экранах 01 и 07: цифры на
# разных экранах одной консоли обязаны сходиться.
DAYS = 7

# Сколько ждём Telegram. Экран не должен зависеть от чужого аптайма
# дольше секунды: это справка, а не операция.
TELEGRAM_TIMEOUT_SEC = 4


@router.get("/channels")
async def channels(session: AsyncSession = Depends(get_session)) -> dict:
    """Состояние каналов для экрана 05."""
    workspace = await get_workspace(session)
    rows = (
        (await session.execute(CHANNELS_SQL, {"ws": workspace.id, "days": DAYS}))
        .mappings()
        .all()
    )
    counts = {row["channel"]: row["conversations"] for row in rows}

    host = settings.PUBLIC_BASE_URL.rstrip("/")
    public = is_filled(settings.PUBLIC_BASE_URL)

    return {
        "days": DAYS,
        "public_base_url": host if public else None,
        "channels": [
            await _telegram(counts.get("telegram", 0)),
            _widget(counts.get("widget", 0), host, public),
            _whatsapp(counts.get("whatsapp", 0)),
        ],
    }


async def _telegram(conversations: int) -> dict:
    bot = settings.TELEGRAM_BOT_USERNAME
    if not is_filled(settings.TELEGRAM_BOT_TOKEN):
        return {
            "id": "telegram",
            "title": "Telegram",
            "state": "off",
            "note": "TELEGRAM_BOT_TOKEN не задан в .env",
            "bot": bot,
            "link": None,
            "conversations": conversations,
        }

    webhook = await _webhook_info()
    public = settings.PUBLIC_BASE_URL.rstrip("/")

    if webhook is None:
        state, note = "unknown", "не удалось спросить Telegram — нет сети?"
    elif webhook["error"]:
        state, note = "wait", f"Telegram не доставляет: {webhook['error']}"
    elif not webhook["url"]:
        # Работает polling или не настроено вовсе. Для демо это «молчит»:
        # QR со стенда откроет бота, который не отвечает.
        state, note = "wait", "вебхук не прописан — scripts/tg_webhook.py --set"
    elif is_filled(settings.PUBLIC_BASE_URL) and not webhook["url"].startswith(public):
        # САМАЯ ЧАСТАЯ ПОЛОМКА ДЕМО. ngrok при перезапуске выдаёт новый
        # адрес; в .env его меняют, а вебхук остаётся на старом. Telegram
        # об этом молчит: ошибок доставки нет, пока никто не написал, —
        # и `getWebhookInfo` показывает бодрое «всё хорошо». Сравнение
        # адресов ловит расхождение до того, как бот замолчит на встрече.
        state = "wait"
        note = f"вебхук смотрит на другой адрес: {webhook['url']}"
    else:
        state, note = "live", f"вебхук: {webhook['url']}"

    return {
        "id": "telegram",
        "title": "Telegram",
        "state": state,
        "note": note,
        "bot": bot,
        "link": f"https://t.me/{bot}",
        "webhook": webhook,
        "conversations": conversations,
    }


async def _webhook_info() -> dict | None:
    """Что Telegram думает о нашем вебхуке. `None` — спросить не вышло."""
    from app.channels.telegram import get_bot

    bot = get_bot()
    try:
        info = await asyncio.wait_for(
            bot.get_webhook_info(), timeout=TELEGRAM_TIMEOUT_SEC
        )
    except Exception as exc:  # noqa: BLE001 — справка не повод ронять экран
        log.warning("getWebhookInfo не ответил: %s", exc)
        return None

    return {
        "url": info.url or "",
        "pending": info.pending_update_count,
        "error": info.last_error_message,
    }


def _widget(conversations: int, host: str, public: bool) -> dict:
    return {
        "id": "widget",
        "title": "Веб-виджет",
        "state": "live" if public else "wait",
        "note": (
            "одна строка в шаблон сайта"
            if public
            else "PUBLIC_BASE_URL не задан — сниппет вставлять некуда"
        ),
        # Сниппет собирается на бэкенде, чтобы адрес в нём был ровно тот,
        # по которому отвечает этот стенд. Скопированный с экрана сниппет
        # обязан работать — иначе он хуже, чем никакого. Пока адреса нет,
        # на его месте стоит явная заглушка: строка «ЗАПОЛНИТЬ-после-ngrok»
        # из .env выглядит как настоящий адрес и однажды уедет в шаблон
        # сайта банка.
        "snippet": (
            f'<script src="{host if public else "https://<адрес-стенда>"}/w.js"\n'
            f'  data-ws="{settings.WORKSPACE_DEFAULT_SLUG}"\n'
            f'  data-lang="tg,ru"></script>'
        ),
        # На экране 05 ведём на страницу-сайт, а не на технический
        # полигон: экран показывают заказчику, и «вот так это выглядит у
        # вас» убедительнее, чем страница с намеренно ломаными стилями.
        "site_url": f"{host}/widget/site" if public else None,
        "demo_url": f"{host}/widget/demo" if public else None,
        "conversations": conversations,
    }


def _whatsapp(conversations: int) -> dict:
    ready = is_filled(settings.WHATSAPP_TOKEN) and is_filled(
        settings.WHATSAPP_PHONE_ID
    )
    return {
        "id": "whatsapp",
        "title": "WhatsApp",
        "state": "wait" if ready else "off",
        "note": (
            "песочница Meta: временный токен живёт 24 часа"
            if ready
            else "канал не подключён: нет токена и номера в .env"
        ),
        "conversations": conversations,
    }

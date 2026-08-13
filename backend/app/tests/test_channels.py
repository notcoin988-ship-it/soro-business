"""Состояние каналов для экрана 05 (`GET /api/channels`).

Экран нужен ровно в одном сценарии: за полчаса до встречи посмотреть, всё
ли живо. Поэтому проверяется не «эндпоинт отвечает 200», а то, что он
говорит ПРАВДУ в каждом из состояний — особенно когда канал сломан.

Telegram здесь подделан: живой `getWebhookInfo` ходит по сети, и тест,
зависящий от чужого аптайма, меряет не наш код.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import channels
from app.config import settings
from app.core import dialog
from app.db import get_session
from app.main import app


@pytest.fixture
def client(session, workspace, monkeypatch):
    monkeypatch.setattr(settings, "WORKSPACE_DEFAULT_SLUG", workspace.slug)
    # По умолчанию: бот настроен, вебхук прописан, ошибок нет.
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123456:TEST")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://stand.example")

    async def fake_webhook():
        return {"url": "https://stand.example/webhooks/telegram", "pending": 0, "error": None}

    monkeypatch.setattr(channels, "_webhook_info", fake_webhook)
    app.dependency_overrides[get_session] = lambda: session
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://console")
    app.dependency_overrides.clear()


async def get(client) -> dict:
    async with client:
        response = await client.get("/api/channels")
    assert response.status_code == 200
    return response.json()


def card(body: dict, channel_id: str) -> dict:
    return next(item for item in body["channels"] if item["id"] == channel_id)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


async def test_configured_telegram_is_live(client):
    body = await get(client)
    telegram = card(body, "telegram")

    assert telegram["state"] == "live"
    assert telegram["link"].startswith("https://t.me/")


async def test_missing_webhook_is_not_live(client, monkeypatch):
    """Вебхук не прописан — QR со стенда откроет бота, который молчит.
    Зелёная плашка в этот момент — худшее, что может показать экран."""

    async def no_webhook():
        return {"url": "", "pending": 0, "error": None}

    monkeypatch.setattr(channels, "_webhook_info", no_webhook)

    telegram = card(await get(client), "telegram")
    assert telegram["state"] == "wait"
    assert "tg_webhook" in telegram["note"]


async def test_delivery_errors_are_shown(client, monkeypatch):
    """Самая частая поломка демо: ngrok сменил адрес, и Telegram копит
    ошибки доставки. Об этом надо сказать словами Telegram."""

    async def broken():
        return {
            "url": "https://old-tunnel.example/webhooks/telegram",
            "pending": 7,
            "error": "Connection refused",
        }

    monkeypatch.setattr(channels, "_webhook_info", broken)

    telegram = card(await get(client), "telegram")
    assert telegram["state"] == "wait"
    assert "Connection refused" in telegram["note"]
    assert telegram["webhook"]["pending"] == 7


async def test_webhook_on_a_stale_address_is_caught(client, monkeypatch):
    """Самая частая поломка демо, и Telegram о ней молчит.

    ngrok при перезапуске выдаёт новый адрес; в .env его меняют, а вебхук
    остаётся на старом. Ошибок доставки при этом нет — пока никто не
    написал боту, — и `getWebhookInfo` бодро отвечает «всё хорошо».
    Ловится сравнением с PUBLIC_BASE_URL.
    """

    async def stale():
        return {
            "url": "https://old-tunnel.ngrok-free.app/webhooks/telegram",
            "pending": 0,
            "error": None,
        }

    monkeypatch.setattr(channels, "_webhook_info", stale)

    telegram = card(await get(client), "telegram")
    assert telegram["state"] == "wait"
    assert "другой адрес" in telegram["note"]


async def test_unreachable_telegram_is_unknown_not_live(client, monkeypatch):
    """Не смогли спросить — значит не знаем. Врать в любую сторону нельзя."""

    async def silent():
        return None

    monkeypatch.setattr(channels, "_webhook_info", silent)

    telegram = card(await get(client), "telegram")
    assert telegram["state"] == "unknown"


async def test_missing_token_turns_the_channel_off(client, monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")

    telegram = card(await get(client), "telegram")
    assert telegram["state"] == "off"
    assert telegram["link"] is None


@pytest.mark.parametrize("placeholder", ["ЗАПРОСИ-У-ТИМЛИДА", "123456:ABC..."])
async def test_placeholder_token_is_not_a_channel(client, monkeypatch, placeholder):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", placeholder)

    assert card(await get(client), "telegram")["state"] == "off"


# ---------------------------------------------------------------------------
# виджет
# ---------------------------------------------------------------------------


async def test_snippet_carries_the_address_of_this_stand(client):
    """Скопированный с экрана сниппет обязан работать: адрес в нём — тот,
    по которому отвечает этот бэкенд, а не выдуманный cdn.sorollm.tj."""
    widget = card(await get(client), "widget")

    assert widget["state"] == "live"
    assert 'src="https://stand.example/w.js"' in widget["snippet"]
    assert widget["site_url"] == "https://stand.example/widget/site"
    assert widget["demo_url"] == "https://stand.example/widget/demo"


async def test_widget_without_public_address_waits(client, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "ЗАПОЛНИТЬ-после-ngrok")

    widget = card(await get(client), "widget")
    assert widget["state"] == "wait"
    assert widget["site_url"] is None
    assert widget["demo_url"] is None
    # Заглушка из .env не должна выглядеть как адрес: скопированная в
    # шаблон сайта банка, она там и останется.
    assert "ЗАПОЛНИТЬ" not in widget["snippet"]


# ---------------------------------------------------------------------------
# WhatsApp и счётчики
# ---------------------------------------------------------------------------


async def test_whatsapp_is_off_until_both_secrets_are_real(client, monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_TOKEN", "EAAGreal")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_ID", "ЗАПРОСИ")

    assert card(await get(client), "whatsapp")["state"] == "off"


async def test_counters_come_from_real_dialogs(client, session, workspace):
    """Число на карточке — диалоги за неделю по каналу ПЕРВОГО сообщения,
    как на экране 07: цифры двух экранов обязаны сходиться."""
    identity = await dialog.resolve_identity(session, workspace.id, "widget", "ch-1")
    conversation = await dialog.resolve_conversation(
        session, workspace.id, identity.contact_id
    )
    await dialog.save_message(
        session,
        conversation=conversation,
        channel="widget",
        role="user",
        text="Салом",
    )

    body = await get(client)
    assert card(body, "widget")["conversations"] == 1
    assert card(body, "telegram")["conversations"] == 0

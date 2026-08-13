"""Обзор экрана 01 (`GET /api/overview`).

Экран первый, и его смотрят дольше всех. Проверяется то, из-за чего он
и переставал быть картинкой: что цифры считаются по базе, что ряды для
спарклайнов не съезжают на днях без диалогов и что чек-лист готовности не
ставит галочку напротив неподключённого канала.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.core import dialog, escalation
from app.db import get_session
from app.main import app


@pytest.fixture
def client(session, workspace, monkeypatch):
    monkeypatch.setattr(settings, "WORKSPACE_DEFAULT_SLUG", workspace.slug)
    app.dependency_overrides[get_session] = lambda: session
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://console")
    app.dependency_overrides.clear()


async def talk(session, workspace, external_id, *, channel="telegram"):
    identity = await dialog.resolve_identity(
        session, workspace.id, channel, external_id
    )
    conversation = await dialog.resolve_conversation(
        session, workspace.id, identity.contact_id
    )
    await dialog.save_message(
        session,
        conversation=conversation,
        channel=channel,
        role="user",
        text="Фоизи амонат чанд аст?",
    )
    return conversation


async def get(client) -> dict:
    async with client:
        response = await client.get("/api/overview")
    assert response.status_code == 200
    return response.json()


async def test_empty_workspace_does_not_crash(client):
    body = await get(client)

    assert body["conversations"]["total"] == 0
    assert body["conversations"]["bot_share"] == 0
    assert body["latency"]["median_ms"] is None
    assert body["citations"]["share"] == 0


async def test_spark_has_a_point_for_every_day(client, session, workspace):
    """Ряд для спарклайна — семь точек, даже если писали только сегодня.

    Без дней-нулей линия соврёт: провал в переписке превратится в ровный
    участок, и «динамика» на первом экране станет украшением.
    """
    await talk(session, workspace, "ov-1")

    body = await get(client)

    assert len(body["conversations"]["spark"]) == 7
    assert len(body["latency"]["spark"]) == 7
    assert len(body["citations"]["spark"]) == 7
    assert body["conversations"]["spark"][-1] == 1
    assert body["conversations"]["spark"][0] == 0


async def test_bot_share_counts_escalations(client, session, workspace):
    await talk(session, workspace, "ov-quiet")
    loud = await talk(session, workspace, "ov-loud")
    await escalation.escalate(session, loud, escalation.REASON_USER_REQUEST)

    body = await get(client)

    assert body["conversations"]["total"] == 2
    assert body["conversations"]["by_bot"] == 1
    assert body["conversations"]["bot_share"] == 50


async def test_citation_share_counts_answers_with_chunks(
    client, session, workspace
):
    """«Ответы со ссылкой на источник» — это доля ответов с непустым
    chunks_used: из него канал и рисует бейджи."""
    conversation = await talk(session, workspace, "ov-cite")
    await dialog.save_message(
        session,
        conversation=conversation,
        channel="telegram",
        role="assistant",
        text="Фоиз 14,5% [1]",
        chunks_used=[1],
        latency_ms=1200,
    )
    await dialog.save_message(
        session,
        conversation=conversation,
        channel="telegram",
        role="assistant",
        text="Соединяю со специалистом",
        latency_ms=800,
    )

    body = await get(client)

    assert body["citations"] == {
        "answers": 2,
        "cited": 1,
        "share": 50,
        "spark": body["citations"]["spark"],
    }
    assert body["latency"]["median_ms"] == 1000


async def test_channels_come_from_real_messages(client, session, workspace):
    """Подпись «Telegram · веб» под первой карточкой — это каналы, из
    которых реально писали, а не список поддерживаемых."""
    await talk(session, workspace, "ov-tg", channel="telegram")
    await talk(session, workspace, "ov-web", channel="widget")

    body = await get(client)

    assert body["conversations"]["channels"] == ["Telegram", "веб"]


# ---------------------------------------------------------------------------
# готовность к пилоту
# ---------------------------------------------------------------------------


async def test_unconnected_whatsapp_has_no_tick(client, monkeypatch):
    """Галочка напротив неподключённого канала — худшее, что может быть
    на первом экране: её увидит правление банка."""
    monkeypatch.setattr(settings, "WHATSAPP_TOKEN", "")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_ID", "")

    body = await get(client)
    whatsapp = next(
        item for item in body["readiness"] if item["title"].startswith("WhatsApp")
    )

    assert whatsapp["done"] is False
    assert "не подключён" in whatsapp["hint"]


async def test_empty_knowledge_base_is_not_ticked(client):
    body = await get(client)
    documents = next(
        item for item in body["readiness"] if item["title"].startswith("Документы")
    )

    assert documents["done"] is False
    assert "пуста" in documents["hint"]


async def test_telegram_tick_follows_the_token(client, monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")

    body = await get(client)
    telegram = next(
        item for item in body["readiness"] if item["title"].startswith("Telegram")
    )

    assert telegram["done"] is False


@pytest.mark.parametrize(
    "placeholder",
    ["ЗАПРОСИ-У-ТИМЛИДА", "поменять-обязательно", "EAAG...", "  "],
)
async def test_placeholder_in_env_is_not_a_setting(client, monkeypatch, placeholder):
    """Заглушка в `.env` — это НЕ настроенный канал.

    Найдено живым прогоном: в файле стенда лежит
    `WHATSAPP_TOKEN=ЗАПРОСИ-У-ТИМЛИДА`, проверка «не пусто» её пропускала,
    и первый экран показывал галочку напротив канала, которого нет.
    """
    monkeypatch.setattr(settings, "WHATSAPP_TOKEN", placeholder)
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_ID", placeholder)

    body = await get(client)
    whatsapp = next(
        item for item in body["readiness"] if item["title"].startswith("WhatsApp")
    )

    assert whatsapp["done"] is False


async def test_widget_needs_a_public_address(client, monkeypatch):
    """Сниппет со строкой «ЗАПОЛНИТЬ-после-ngrok» вставлять некуда."""
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "ЗАПОЛНИТЬ-после-ngrok")

    body = await get(client)
    widget = next(
        item for item in body["readiness"] if item["title"].startswith("Веб-виджет")
    )

    assert widget["done"] is False
    assert "ngrok" in widget["hint"]

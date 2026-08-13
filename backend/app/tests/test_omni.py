"""Живой диалог для экрана 04 (`GET /api/omni/latest`).

Эндпоинта нет в приложении А: экран 04 по ТЗ презентационный. Он появился,
чтобы рядом с нарисованным сценарием показывать разговор, который
действительно случился, — и проверять надо ровно то, ради чего он есть:
что выбирается диалог с БОЛЬШИМ ЧИСЛОМ КАНАЛОВ, что видны обе
идентичности одного человека и что наружу уходит маска, а не оригинал.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.core import dialog
from app.db import get_session
from app.main import app

CARD = "5058123456789012"


@pytest.fixture
def client(session, workspace, monkeypatch):
    """Клиент, для которого воркспейс по умолчанию — тестовый.

    Эндпоинт всегда смотрит в воркспейс из `.env`, то есть в демо-базу
    разработки. Тест, который на неё опирается, меряет чужие данные:
    сегодня там два канала, завтра появится третий — и проверка «победил
    двухканальный диалог» развалится не по вине кода. Подменяем slug и
    работаем со своим воркспейсом, пустым и предсказуемым.
    """
    monkeypatch.setattr(settings, "WORKSPACE_DEFAULT_SLUG", workspace.slug)
    app.dependency_overrides[get_session] = lambda: session
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://console")
    app.dependency_overrides.clear()


async def talk(session, workspace, channel, external_id, *messages, role="user"):
    """Разговор в одном канале. Возвращает диалог."""
    identity = await dialog.resolve_identity(
        session, workspace.id, channel, external_id
    )
    conversation = await dialog.resolve_conversation(
        session, workspace.id, identity.contact_id
    )
    for text in messages:
        await dialog.save_message(
            session,
            conversation=conversation,
            channel=channel,
            role=role,
            text=text,
        )
    return conversation


async def test_no_dialogs_yet_is_not_an_error(client):
    """Разговоров ещё не было — это не ошибка.

    Экран должен сказать «живого диалога пока нет» и объяснить, как его
    завести, а не показать пустые корпуса или красную плашку.
    """
    async with client:
        response = await client.get("/api/omni/latest")

    assert response.status_code == 200
    body = response.json()
    assert body["empty"] is True
    assert body["messages"] == []


async def test_two_channel_dialog_wins(client, session, workspace):
    """Диалог из двух каналов важнее свежего одноканального.

    Экран 04 про омниканальность: показать там разговор, который весь
    случился в Telegram, значит показать ровно то, чего экран не обещает.
    """
    contact_conversation = await talk(
        session, workspace, "telegram", "omni-tg", "Фоизи амонат чанд аст?"
    )
    identity = await dialog.resolve_identity(
        session, workspace.id, "widget", "omni-widget"
    )
    # Тот же контакт — как после склейки по link_token.
    identity.contact_id = contact_conversation.contact_id
    await session.flush()
    await dialog.save_message(
        session,
        conversation=contact_conversation,
        channel="widget",
        role="user",
        text="А если сниму раньше срока?",
    )

    # Одноканальный диалог, заведённый ПОЗЖЕ — он не должен победить.
    await talk(session, workspace, "telegram", "omni-tg-2", "Салом")

    async with client:
        body = (await client.get("/api/omni/latest")).json()

    assert body["empty"] is False
    assert body["channels"] == ["telegram", "widget"]
    assert body["conversation_id"] == contact_conversation.id
    assert {i["channel"] for i in body["identities"]} == {"telegram", "widget"}


async def test_personal_data_leaves_masked(client, session, workspace):
    """Экран 04 показывают с проектора — номер карты туда не попадает."""
    conversation = await talk(
        session, workspace, "telegram", "omni-pii", f"Корти ман {CARD}"
    )
    identity = await dialog.resolve_identity(
        session, workspace.id, "widget", "omni-pii-w"
    )
    identity.contact_id = conversation.contact_id
    await session.flush()
    await dialog.save_message(
        session,
        conversation=conversation,
        channel="widget",
        role="user",
        text="Помогите с картой",
    )

    async with client:
        body = (await client.get("/api/omni/latest")).json()

    texts = " ".join(message["text"] for message in body["messages"])
    assert CARD not in texts
    assert "[CARD]" in texts

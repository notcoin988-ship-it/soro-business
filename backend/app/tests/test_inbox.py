"""Инбокс оператора: API и сокет (раздел 8.3 ТЗ).

Правило проекта — минимум один pytest на эндпоинт. Плюс два инварианта,
которые дороже остального: оператор видит ОРИГИНАЛ сообщения (он для того
и человек), а в общей очереди показывается маска; и ответ оператора уходит
в тот канал, откуда клиент писал.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core import dialog, escalation
from app.db import get_session
from app.main import app
from app.models import Conversation, Escalation, Message

WS = "test-ws"
CARD = "5058123456789012"


@pytest.fixture
async def client(session):
    app.dependency_overrides[get_session] = lambda: session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def silent_telegram(monkeypatch):
    """Ответ оператора не должен стучаться в настоящий Telegram из тестов."""
    sent: list[dict] = []

    async def fake_send(session, conversation, channel, text):
        sent.append({"channel": channel, "text": text})

    monkeypatch.setattr("app.api.inbox._send_to_channel", fake_send)
    return sent


async def send(session, text: str, *, external_id="tg-inbox"):
    return await dialog.handle_incoming(
        session,
        channel="telegram",
        external_id=external_id,
        text=text,
        workspace_slug=WS,
    )


async def only_conversation(session, workspace) -> Conversation:
    # база общая с разработкой — фильтруем по своему воркспейсу
    return await session.scalar(
        select(Conversation).where(Conversation.workspace_id == workspace.id)
    )


async def escalated(session, workspace) -> Conversation:
    await send(session, f"Корти ман {CARD} кор намекунад, оператор лозим")
    return await only_conversation(session, workspace)


# ---------------------------------------------------------------------------
# очередь
# ---------------------------------------------------------------------------


async def test_waiting_queue_shows_new_escalation(client, session, workspace):
    conversation = await escalated(session, workspace)

    cards = (await client.get("/api/inbox", params={"status": "waiting"})).json()
    mine = [c for c in cards if c["conversation_id"] == conversation.id]

    assert len(mine) == 1
    assert mine[0]["reason"] == "user_request"
    assert mine[0]["channel"] == "telegram"
    assert mine[0]["taken_by"] is None


async def test_queue_preview_is_masked(client, session, workspace):
    """В общей очереди номер карты светить незачем — диалог ещё не открыт."""
    conversation = await escalated(session, workspace)

    cards = (await client.get("/api/inbox", params={"status": "waiting"})).json()
    mine = next(c for c in cards if c["conversation_id"] == conversation.id)

    assert CARD not in mine["preview"]
    assert "[CARD]" in mine["preview"]


async def test_taken_moves_card_to_active(client, session, workspace):
    conversation = await escalated(session, workspace)
    await client.post(f"/api/conversations/{conversation.id}/take")

    waiting = (await client.get("/api/inbox", params={"status": "waiting"})).json()
    active = (await client.get("/api/inbox", params={"status": "active"})).json()

    assert conversation.id not in [c["conversation_id"] for c in waiting]
    assert conversation.id in [c["conversation_id"] for c in active]


async def test_resolved_moves_card_to_resolved(client, session, workspace):
    conversation = await escalated(session, workspace)
    await client.post(f"/api/conversations/{conversation.id}/resolve")

    resolved = (await client.get("/api/inbox", params={"status": "resolved"})).json()
    assert conversation.id in [c["conversation_id"] for c in resolved]


async def test_unknown_status_rejected(client):
    assert (await client.get("/api/inbox", params={"status": "нет"})).status_code == 422


async def test_counters_for_menu_badge(client, session, workspace):
    await escalated(session, workspace)
    counters = (await client.get("/api/inbox/counters")).json()
    assert counters["waiting"] >= 1
    assert set(counters) == {"waiting", "active"}


# ---------------------------------------------------------------------------
# карточка диалога
# ---------------------------------------------------------------------------


async def test_conversation_shows_original_text_to_operator(
    client, session, workspace
):
    """Оператор видит НАСТОЯЩИЙ номер карты.

    Инвариант проекта с двух сторон: в модель уходит `text_masked`, а
    человеку показывается `text`. Перепутать — самая дорогая ошибка.
    """
    conversation = await escalated(session, workspace)

    card = (await client.get(f"/api/conversations/{conversation.id}")).json()
    client_messages = [m for m in card["messages"] if m["role"] == "user"]

    assert any(CARD in m["text"] for m in client_messages)


async def test_conversation_lists_channels_of_contact(client, session, workspace):
    """Омниканальность в данных: один диалог, каналы перечислены."""
    conversation = await escalated(session, workspace)
    card = (await client.get(f"/api/conversations/{conversation.id}")).json()
    assert card["channels"] == ["telegram"]


async def test_conversation_carries_escalation(client, session, workspace):
    conversation = await escalated(session, workspace)
    card = (await client.get(f"/api/conversations/{conversation.id}")).json()

    assert card["escalation"]["reason"] == "user_request"
    assert card["status"] == "operator"


async def test_unknown_conversation_is_404(client):
    assert (await client.get("/api/conversations/999999")).status_code == 404


async def test_hint_is_empty_without_rag(client, session, workspace):
    """База знаний пуста — подсказывать нечем, но и падать не за что."""
    conversation = await escalated(session, workspace)
    card = (await client.get(f"/api/conversations/{conversation.id}")).json()
    assert card["hint"] == []


# ---------------------------------------------------------------------------
# действия оператора
# ---------------------------------------------------------------------------


async def test_take_sets_operator(client, session, workspace):
    conversation = await escalated(session, workspace)

    response = await client.post(
        f"/api/conversations/{conversation.id}/take", json={"operator": "manija"}
    )

    assert response.status_code == 200
    assert response.json()["taken_by"] == "manija"


async def test_take_without_escalation_is_409(client, session, workspace):
    """Диалог не ждёт оператора — понятная ошибка, а не 500."""
    await send(session, "Салом")
    conversation = await only_conversation(session, workspace)
    await escalation.resolve(session, conversation)
    await session.commit()

    response = await client.post(f"/api/conversations/{conversation.id}/take")
    assert response.status_code == 409


async def test_reply_goes_to_client_channel(client, session, workspace, silent_telegram):
    """Ответ уходит в тот канал, откуда клиент писал (раздел 8.3)."""
    conversation = await escalated(session, workspace)

    response = await client.post(
        f"/api/conversations/{conversation.id}/reply",
        json={"text": "Ҳозир тафтиш мекунам"},
    )

    assert response.status_code == 200
    assert response.json()["channel"] == "telegram"
    assert silent_telegram == [
        {"channel": "telegram", "text": "Ҳозир тафтиш мекунам"}
    ]


async def test_reply_is_saved_as_operator_message(client, session, workspace):
    conversation = await escalated(session, workspace)
    await client.post(
        f"/api/conversations/{conversation.id}/reply", json={"text": "Салом!"}
    )

    saved = await session.scalar(
        select(Message)
        .where(Message.conversation_id == conversation.id, Message.role == "operator")
        .order_by(Message.id.desc())
    )
    assert saved is not None
    assert saved.text == "Салом!"


async def test_empty_reply_rejected(client, session, workspace):
    conversation = await escalated(session, workspace)
    response = await client.post(
        f"/api/conversations/{conversation.id}/reply", json={"text": "   "}
    )
    assert response.status_code == 422


async def test_resolve_returns_to_bot(client, session, workspace):
    conversation = await escalated(session, workspace)

    response = await client.post(f"/api/conversations/{conversation.id}/resolve")

    assert response.json()["status"] == "bot"
    item = await session.scalar(
        select(Escalation).where(Escalation.conversation_id == conversation.id)
    )
    assert item.resolved_at is not None


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


def test_socket_receives_new_escalation():
    """Экран 06 узнаёт о новой эскалации без опроса — на этом звук.

    Синхронный тест: TestClient поднимает своё приложение и свой цикл,
    поэтому шлём событие напрямую через `escalation.notify`, а не через
    путь сообщения — проверяется доставка, а не то, кто её вызвал.
    """
    import asyncio

    from fastapi.testclient import TestClient

    with TestClient(app) as http:
        with http.websocket_connect("/ws/inbox") as socket:
            asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
                escalation.notify(
                    "new_escalation", {"conversation_id": 1, "reason": "user_request"}
                )
            )
            event = socket.receive_json()

    assert event["event"] == "new_escalation"
    assert event["reason"] == "user_request"

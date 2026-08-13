"""Закрытие диалога оператором и оценка его работы клиентом.

Оценка живёт в таблице `feedback` из раздела 5 — той самой, про которую в
списке «не назначено никому» стоял вопрос «либо она мертва, либо кто-то
должен её закрыть». Здесь она закрыта: палец вверх или вниз на прощальное
сообщение оператора.

ПОЧЕМУ НЕ ПЯТЬ ЗВЁЗД, хотя прототип обещает «4,4/5»: в DDL стоит
`CHECK (score IN (-1, 1))`. Разбор противоречия — в шапке
`core/feedback.py`.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.channels import widget
from app.config import settings
from app.core import dialog, escalation
from app.db import get_session
from app.main import app
from app.models import Conversation, Feedback, Message

UID = "rate-uid-1"


@pytest.fixture
def client(session, workspace, monkeypatch):
    monkeypatch.setattr(settings, "WORKSPACE_DEFAULT_SLUG", workspace.slug)
    app.dependency_overrides[get_session] = lambda: session
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://console")
    app.dependency_overrides.clear()
    widget._streams.clear()


async def escalated_dialog(session, workspace, *, channel="widget", uid=UID):
    """Клиент написал, бот сдался, оператор взял диалог в работу."""
    identity = await dialog.resolve_identity(session, workspace.id, channel, uid)
    conversation = await dialog.resolve_conversation(
        session, workspace.id, identity.contact_id
    )
    await dialog.save_message(
        session,
        conversation=conversation,
        channel=channel,
        role="user",
        text="Помогите с картой",
    )
    await escalation.escalate(session, conversation, escalation.REASON_USER_REQUEST)
    await escalation.take(session, conversation, "operator")
    await session.flush()
    return conversation


# ---------------------------------------------------------------------------
# закрытие диалога
# ---------------------------------------------------------------------------


async def test_close_ends_the_conversation(client, session, workspace):
    """После закрытия следующее сообщение клиента начнёт НОВЫЙ диалог —
    в этом и разница с «вернуть боту»."""
    conversation = await escalated_dialog(session, workspace)

    async with client:
        body = (
            await client.post(f"/api/conversations/{conversation.id}/close")
        ).json()

    assert body["status"] == "closed"
    await session.refresh(conversation)
    assert conversation.status == "closed"
    assert conversation.closed_at is not None

    identity_conversation = await dialog.resolve_conversation(
        session, workspace.id, conversation.contact_id
    )
    assert identity_conversation.id != conversation.id


async def test_close_says_goodbye_and_asks_for_a_rating(client, session, workspace):
    """Клиент должен увидеть прощание и понять, что его просят оценить."""
    conversation = await escalated_dialog(session, workspace)

    async with client:
        body = (
            await client.post(f"/api/conversations/{conversation.id}/close")
        ).json()

    last = await session.scalar(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.id.desc())
    )
    assert last.role == "operator"
    assert "Оцените" in last.text
    assert body["rate_for"] == last.id


async def test_closing_twice_is_a_conflict(client, session, workspace):
    conversation = await escalated_dialog(session, workspace)

    async with client:
        await client.post(f"/api/conversations/{conversation.id}/close")
        second = await client.post(f"/api/conversations/{conversation.id}/close")

    assert second.status_code == 409


async def test_close_pushes_the_event_into_the_widget(client, session, workspace):
    """Виджет узнаёт о закрытии из своего потока — по этому событию он и
    показывает кнопки оценки."""
    conversation = await escalated_dialog(session, workspace)
    seen: list[tuple] = []
    widget._streams.setdefault(UID, set())

    import asyncio

    queue: asyncio.Queue = asyncio.Queue()
    widget._streams[UID].add(queue)

    async with client:
        await client.post(f"/api/conversations/{conversation.id}/close")

    while not queue.empty():
        seen.append(queue.get_nowait())

    events = [name for name, _ in seen]
    assert "operator_msg" in events
    assert "closed" in events
    closed = next(data for name, data in seen if name == "closed")
    assert closed["rate_for"]


# ---------------------------------------------------------------------------
# оценка
# ---------------------------------------------------------------------------


async def test_client_rates_the_operator(client, session, workspace):
    conversation = await escalated_dialog(session, workspace)

    async with client:
        closed = (
            await client.post(f"/api/conversations/{conversation.id}/close")
        ).json()
        response = await client.post(
            "/widget/feedback",
            json={"uid": UID, "message_id": closed["rate_for"], "score": 1},
        )

    assert response.status_code == 200
    saved = await session.scalar(select(Feedback))
    assert saved.score == 1
    assert saved.message_id == closed["rate_for"]


async def test_second_click_replaces_the_first(client, session, workspace):
    """Оценку ставят один раз: вторая перезаписывает первую, а не копится.

    Иначе один человек с двумя вкладками накрутит статистику в любую
    сторону.
    """
    conversation = await escalated_dialog(session, workspace)

    async with client:
        closed = (
            await client.post(f"/api/conversations/{conversation.id}/close")
        ).json()
        await client.post(
            "/widget/feedback",
            json={"uid": UID, "message_id": closed["rate_for"], "score": 1},
        )
        await client.post(
            "/widget/feedback",
            json={"uid": UID, "message_id": closed["rate_for"], "score": -1},
        )

    rows = (await session.scalars(select(Feedback))).all()
    assert len(rows) == 1
    assert rows[0].score == -1


async def test_score_outside_the_check_is_rejected(client, session, workspace):
    """Пять баллов в колонку с `CHECK (score IN (-1,1))` не положить —
    отказываем на входе, а не ловим ошибку базы."""
    conversation = await escalated_dialog(session, workspace)

    async with client:
        closed = (
            await client.post(f"/api/conversations/{conversation.id}/close")
        ).json()
        response = await client.post(
            "/widget/feedback",
            json={"uid": UID, "message_id": closed["rate_for"], "score": 5},
        )

    assert response.status_code == 422
    assert await session.scalar(select(Feedback)) is None


async def test_cannot_rate_a_stranger_dialog(client, session, workspace):
    """Эндпоинт открыт наружу: оценить чужую переписку, зная номер строки
    в `messages`, нельзя."""
    conversation = await escalated_dialog(session, workspace)
    await dialog.resolve_identity(session, workspace.id, "widget", "rate-uid-2")

    async with client:
        closed = (
            await client.post(f"/api/conversations/{conversation.id}/close")
        ).json()
        response = await client.post(
            "/widget/feedback",
            json={"uid": "rate-uid-2", "message_id": closed["rate_for"], "score": 1},
        )

    assert response.status_code == 403


async def test_rating_shows_up_in_analytics(client, session, workspace):
    """Карточка «Оценка работы оператора» на экране 07 считает те же
    строки, что положил виджет."""
    conversation = await escalated_dialog(session, workspace)

    async with client:
        closed = (
            await client.post(f"/api/conversations/{conversation.id}/close")
        ).json()
        await client.post(
            "/widget/feedback",
            json={"uid": UID, "message_id": closed["rate_for"], "score": 1},
        )
        analytics = (await client.get("/api/analytics?days=7")).json()

    assert analytics["rating"] == {"total": 1, "positive": 1, "share": 100}


# ---------------------------------------------------------------------------
# оценка из Telegram
# ---------------------------------------------------------------------------


async def test_telegram_button_stores_the_rating(client, session, workspace, monkeypatch):
    """В Telegram оценка приходит кнопкой, а не текстом: попроси написать
    «5» — и это «5» уедет в поиск по базе знаний обычным вопросом."""
    from contextlib import asynccontextmanager

    from app.channels import telegram

    @asynccontextmanager
    async def factory():
        yield session

    monkeypatch.setattr(telegram, "SessionLocal", factory)

    conversation = await escalated_dialog(session, workspace, channel="telegram")
    async with client:
        closed = (
            await client.post(f"/api/conversations/{conversation.id}/close")
        ).json()

    keyboard = telegram.rating_keyboard(closed["rate_for"])
    data = keyboard.inline_keyboard[0][0].callback_data

    assert await telegram.store_rating(data) is True
    saved = await session.scalar(select(Feedback))
    assert saved.score == 1


async def test_foreign_callback_is_ignored(session, monkeypatch):
    """Чужой callback (кнопка другого модуля) не должен ничего писать."""
    from app.channels import telegram

    assert await telegram.store_rating("menu:open") is False


async def test_closed_dialog_leaves_the_operator_queue(client, session, workspace):
    conversation = await escalated_dialog(session, workspace)

    async with client:
        before = (await client.get("/api/inbox?status=active")).json()
        await client.post(f"/api/conversations/{conversation.id}/close")
        after = (await client.get("/api/inbox?status=active")).json()
        resolved = (await client.get("/api/inbox?status=resolved")).json()

    assert len(before) - len(after) == 1
    assert any(
        item["conversation_id"] == conversation.id for item in resolved
    )

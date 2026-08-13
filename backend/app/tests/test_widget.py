"""Эндпоинты веб-виджета (раздел 7.2 ТЗ).

Проверяется транспорт, а не ядро: что пришло от браузера, во что
превратилось для `core.dialog` и что уехало обратно в поток. Логика
ответа проверена в test_dialog.py, и тянуть сюда модель незачем.

ПРО SSE. Поток виджета не заканчивается сам — он живёт, пока открыта
вкладка. Поэтому тесты читают из него ровно столько событий, сколько
ждут, и закрывают его сами; ограничение по времени обязательно, иначе
при поломке тест висел бы до конца прогона.

Читаем поток НЕ через httpx: его ASGI-транспорт копит тело целиком и
отдаёт после того, как приложение закончило отвечать (`asgi.py`,
`body_parts`). Для площадки это работает — её поток кончается вместе с
ответом, — а бесконечный поток виджета так не прочитать никогда. Поэтому
берём `StreamingResponse.body_iterator` у эндпоинта напрямую: проверяется
ровно то, что уедет в браузер.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.channels import widget
from app.core import dialog
from app.core.dialog import Reply
from app.db import get_session
from app.main import app
from app.models import ChannelIdentity
from app.tests.test_link_token import FakeRedis

UID = "3f2b1c4d-uuid-из-localstorage"

# Сколько ждём событий из потока. Секунды с запасом: всё, что дольше, —
# уже поломка, а не медленный прогон.
WAIT = 5


@pytest.fixture
def client(session, monkeypatch):
    """Клиент к приложению с тестовой сессией вместо настоящей.

    Подменять приходится в двух местах: зависимость FastAPI для обычных
    ручек и `SessionLocal` внутри модуля — фоновая задача ответа и поток
    открывают сессию сами, запроса у них уже нет.
    """

    @asynccontextmanager
    async def factory():
        yield session

    monkeypatch.setattr(widget, "SessionLocal", factory)
    app.dependency_overrides[get_session] = lambda: session
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://widget")
    app.dependency_overrides.clear()
    widget._streams.clear()


@pytest.fixture
def core(monkeypatch):
    """Заглушка ядра: отдаёт ответ кусками, как настоящая модель."""
    calls: list[dict] = []

    async def fake_handle(session, **kwargs):
        calls.append(kwargs)
        if kwargs["text"] == "молчи":
            return None
        on_delta = kwargs.get("on_delta")
        if on_delta is not None:
            on_delta("Фоизи солона — ")
            on_delta("14,5%.")
        return Reply(
            text="Фоизи солона — 14,5%. [1]",
            conversation_id=1,
            message_id=2,
            latency_ms=7,
            chunks_used=[],
        )

    monkeypatch.setattr(dialog, "handle_incoming", fake_handle)
    return calls


@pytest.fixture
def redis(monkeypatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(widget, "_redis", lambda: fake)
    return fake


async def read_events(uid: str, count: int, ws: str | None = None) -> list[tuple]:
    """Открыть поток и забрать первые `count` событий, потом закрыть."""
    response = await widget.stream(uid=uid, ws=ws)
    assert response.media_type == "text/event-stream"

    events: list[tuple] = []
    frames = response.body_iterator
    try:
        async for frame in frames:
            if frame.startswith(":"):  # keepalive
                continue
            head, _, body = frame.partition("\n")
            events.append(
                (head[len("event: ") :], json.loads(body[len("data: ") :]))
            )
            if len(events) >= count:
                break
    finally:
        # Закрытие генератора — это и есть «клиент закрыл вкладку»: в нём
        # отрабатывает finally, который отписывает очередь.
        await frames.aclose()
    return events


# ---------------------------------------------------------------------------
# приём сообщения
# ---------------------------------------------------------------------------


async def test_post_returns_202_immediately(client, core, workspace):
    """Браузер не ждёт генерации: ответ приедет в поток."""
    async with client:
        response = await client.post(
            "/widget/messages",
            json={"uid": UID, "text": "Фоизи амонат чанд аст?", "ws": workspace.slug},
        )

    assert response.status_code == 202
    assert response.json()["message_id"]


async def test_empty_question_is_rejected(client, core):
    async with client:
        response = await client.post(
            "/widget/messages", json={"uid": UID, "text": "   "}
        )
    assert response.status_code == 422


async def test_uid_is_required(client, core):
    async with client:
        response = await client.post("/widget/messages", json={"uid": " ", "text": "?"})
    assert response.status_code == 422


async def test_question_goes_to_core_as_widget_channel(client, core, workspace):
    async with client:
        await client.post(
            "/widget/messages",
            json={"uid": UID, "text": "Салом", "ws": workspace.slug},
        )
        await asyncio.sleep(0)  # даём фоновой задаче дойти до ядра

    assert core[0]["channel"] == "widget"
    assert core[0]["external_id"] == UID
    assert core[0]["text"] == "Салом"


# ---------------------------------------------------------------------------
# поток
# ---------------------------------------------------------------------------


async def test_stream_starts_with_history(client, core, session, workspace):
    """Первое событие — вся переписка. Это и есть «виджет помнит диалог»
    из сценария экрана 04."""
    identity = await dialog.resolve_identity(
        session, workspace.id, "widget", UID
    )
    conversation = await dialog.resolve_conversation(
        session, workspace.id, identity.contact_id
    )
    await dialog.save_message(
        session,
        conversation=conversation,
        channel="widget",
        role="user",
        text="Фоизи амонат чанд аст?",
    )

    events = await asyncio.wait_for(read_events(UID, 1, workspace.slug), WAIT)

    assert events[0][0] == "history"
    assert [m["text"] for m in events[0][1]["messages"]] == [
        "Фоизи амонат чанд аст?"
    ]


async def test_new_client_gets_empty_history(client, core):
    events = await asyncio.wait_for(read_events(UID, 1), WAIT)

    assert events[0] == ("history", {"messages": []})


async def test_answer_arrives_as_delta_then_final(client, core, workspace):
    """Куски идут по мере генерации, `final` закрывает ответ.

    Порядок обязателен: виджет рисует текст по кускам, а `final` заменяет
    показанное — ядро могло подменить ответ фразой об эскалации уже после
    того, как поток закончился.
    """

    async def ask():
        # Ждём, пока поток подпишется: событие, отправленное раньше,
        # никто не услышит — это не очередь с историей.
        while UID not in widget._streams:
            await asyncio.sleep(0)
        await client.post(
            "/widget/messages",
            json={"uid": UID, "text": "Фоизи амонат чанд аст?", "ws": workspace.slug},
        )

    async with client:
        asking = asyncio.create_task(ask())
        events = await asyncio.wait_for(read_events(UID, 4, workspace.slug), WAIT)
        await asking

    assert [name for name, _ in events] == ["history", "delta", "delta", "final"]
    assert events[1][1]["text"] == "Фоизи солона — "
    assert events[3][1]["text"] == "Фоизи солона — 14,5%. [1]"


async def test_operator_reply_reaches_the_client(client, core):
    """Оператор ответил из инбокса — реплика приходит в тот же поток."""

    async def answer_as_operator():
        while UID not in widget._streams:
            await asyncio.sleep(0)
        widget.publish(UID, "operator_msg", {"text": "Далер, здравствуйте!"})

    operator = asyncio.create_task(answer_as_operator())
    events = await asyncio.wait_for(read_events(UID, 2), WAIT)
    await operator

    assert events[1] == ("operator_msg", {"text": "Далер, здравствуйте!"})


async def test_stream_forgets_the_client_after_disconnect(client, core):
    """Закрытая вкладка не должна оставлять очередь навсегда."""
    await asyncio.wait_for(read_events(UID, 1), WAIT)

    assert UID not in widget._streams


# ---------------------------------------------------------------------------
# «Продолжить в Telegram»
# ---------------------------------------------------------------------------


async def test_link_token_returns_link_to_the_bot(client, core, redis, workspace):
    async with client:
        response = await client.post(
            "/widget/link-token", json={"uid": UID, "ws": workspace.slug}
        )

    body = response.json()
    assert response.status_code == 200
    assert body["url"].startswith("https://t.me/")
    assert body["token"] in body["url"]


async def test_link_token_works_before_the_first_word(
    client, core, redis, session, workspace
):
    """Кнопку можно нажать, не написав ни слова: клиент решил дочитать с
    телефона. Идентичность для этого заводим на месте."""
    async with client:
        response = await client.post(
            "/widget/link-token", json={"uid": UID, "ws": workspace.slug}
        )

    identity = await session.scalar(
        select(ChannelIdentity).where(
            ChannelIdentity.workspace_id == workspace.id,
            ChannelIdentity.channel == "widget",
            ChannelIdentity.external_id == UID,
        )
    )
    assert identity is not None
    assert widget.take_token(response.json()["token"]) == identity.id

"""Передача диалога оператору (раздел 8.1 ТЗ).

Проверяется то, что нельзя увидеть глазами на демо: не плодятся ли
карточки в инбоксе от одного клиента, не перехватывают ли операторы
диалоги друг у друга, возвращается ли диалог боту.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core import dialog, escalation
from app.models import Conversation, Escalation

WS = "test-ws"


async def send(session, text: str, *, external_id="tg-esc"):
    return await dialog.handle_incoming(
        session,
        channel="telegram",
        external_id=external_id,
        text=text,
        workspace_slug=WS,
    )


async def count_escalations(session, workspace) -> int:
    return await session.scalar(
        select(func.count())
        .select_from(Escalation)
        .where(Escalation.workspace_id == workspace.id)
    )


async def only_conversation(session, workspace) -> Conversation:
    """Диалог ТОЛЬКО своего воркспейса.

    База общая с разработкой: `select(Conversation)` без фильтра берёт
    первый попавшийся диалог из демо-воркспейса, и тест начинает
    проверять чужие данные.
    """
    return await session.scalar(
        select(Conversation).where(Conversation.workspace_id == workspace.id)
    )


async def only_escalation(session, workspace) -> Escalation:
    return await session.scalar(
        select(Escalation)
        .where(Escalation.workspace_id == workspace.id)
        .order_by(Escalation.id.desc())
    )


# ---------------------------------------------------------------------------
# причины
# ---------------------------------------------------------------------------


def test_reasons_match_ddl():
    """Список причин в коде и CHECK в базе обязаны совпадать.

    Разъедутся — узнаем об этом исключением базы на боевом сообщении
    клиента, а не здесь.
    """
    assert escalation.REASONS == {
        "no_answer",
        "low_confidence",
        "user_request",
        "pii_topic",
    }


def test_unknown_reason_collapses_to_no_answer():
    """`llm_unavailable` в DDL нет: авария инфраструктуры для оператора —
    тот же «бот не смог ответить». Подробность остаётся в Reply.reason."""
    assert escalation.normalize_reason("llm_unavailable") == "no_answer"
    assert escalation.normalize_reason(None) == "no_answer"
    assert escalation.normalize_reason("pii_topic") == "pii_topic"


# ---------------------------------------------------------------------------
# создание
# ---------------------------------------------------------------------------


async def test_user_request_creates_escalation(session, workspace):
    """Клиент попросил человека — в инбоксе появляется карточка."""
    reply = await send(session, "Хочу поговорить с оператором")

    assert reply.escalated
    assert reply.reason == "user_request"
    assert await count_escalations(session, workspace) == 1

    item = await only_escalation(session, workspace)
    assert item.reason == "user_request"
    assert item.taken_by is None
    assert item.resolved_at is None


async def test_no_answer_creates_escalation(session, workspace):
    """База знаний пуста — бот сдаётся, диалог уходит оператору."""
    await send(session, "Фоизи амонат чанд аст?")

    item = await only_escalation(session, workspace)
    assert item is not None
    assert item.reason == "no_answer"


async def test_repeat_messages_do_not_duplicate_cards(session, workspace):
    """Три сообщения подряд — одна карточка, а не три.

    Иначе клиент, который нервничает и пишет ещё раз, превращается в
    три строки очереди и три звонка звука у оператора.
    """
    await send(session, "Хочу оператора")
    conversation = await only_conversation(session, workspace)
    # бот молчит, но сообщения всё равно приходят
    await send(session, "Ау?")
    await send(session, "Есть кто?")

    assert await count_escalations(session, workspace) == 1
    assert conversation.status == "operator"


async def test_conversation_switches_to_operator(session, workspace):
    await send(session, "Позовите человека")
    conversation = await only_conversation(session, workspace)
    assert conversation.status == "operator"


# ---------------------------------------------------------------------------
# взять в работу
# ---------------------------------------------------------------------------


async def test_take_marks_operator(session, workspace):
    await send(session, "Хочу оператора")
    conversation = await only_conversation(session, workspace)

    item = await escalation.take(session, conversation, "manija")

    assert item.taken_by == "manija"
    assert item.taken_at is not None


async def test_second_take_does_not_steal(session, workspace):
    """Двое операторов не должны писать клиенту одновременно.

    Первый взял — второй видит, что диалог занят, и `taken_by` не
    меняется.
    """
    await send(session, "Хочу оператора")
    conversation = await only_conversation(session, workspace)

    await escalation.take(session, conversation, "manija")
    item = await escalation.take(session, conversation, "далер")

    assert item.taken_by == "manija"


async def test_take_without_escalation_returns_none(session, workspace):
    """Брать нечего — не падаем, отвечаем «нет».

    API превращает это в 409, а не в 500.
    """
    conversation = Conversation(workspace_id=workspace.id, contact_id=None)
    assert await escalation.take(session, conversation, "manija") is None


# ---------------------------------------------------------------------------
# вернуть боту
# ---------------------------------------------------------------------------


async def test_resolve_returns_dialog_to_bot(session, workspace):
    """«Вернуть боту»: статус обратно в `bot`, эскалация закрыта.

    До этой правки диалог, ушедший оператору, залипал навсегда — бот
    молчал на все следующие сообщения.
    """
    await send(session, "Хочу оператора")
    conversation = await only_conversation(session, workspace)

    item = await escalation.resolve(session, conversation)

    assert conversation.status == "bot"
    assert item.resolved_at is not None


async def test_bot_answers_again_after_resolve(session, workspace):
    """Смысл возврата: бот снова отвечает на сообщения клиента."""
    await send(session, "Хочу оператора")
    conversation = await only_conversation(session, workspace)
    await escalation.resolve(session, conversation)
    await session.commit()

    reply = await send(session, "Фоизи амонат чанд аст?")

    assert reply is not None, "бот молчит после возврата"


async def test_resolve_then_escalate_creates_new_card(session, workspace):
    """Закрытая эскалация не мешает завести новую при следующем поводе."""
    await send(session, "Хочу оператора")
    conversation = await only_conversation(session, workspace)
    await escalation.resolve(session, conversation)
    await session.commit()

    await send(session, "Всё-таки позовите оператора")

    assert await count_escalations(session, workspace) == 2


# ---------------------------------------------------------------------------
# уведомления
# ---------------------------------------------------------------------------


async def test_listeners_receive_events(session, workspace):
    """Инбокс узнаёт о новой эскалации — на этом держится звук на экране 06."""
    seen: list[tuple[str, dict]] = []

    async def listener(event, payload):
        seen.append((event, payload))

    escalation.subscribe(listener)
    try:
        await send(session, "Хочу оператора")
    finally:
        escalation.unsubscribe(listener)

    assert [event for event, _ in seen] == ["new_escalation"]
    assert seen[0][1]["reason"] == "user_request"


async def test_broken_listener_does_not_break_dialog(session, workspace):
    """Оборванный сокет оператора не должен ронять обработку сообщения
    клиента: клиент ждёт ответа и не виноват в чужом разрыве."""

    async def broken(event, payload):
        raise RuntimeError("сокет отвалился")

    escalation.subscribe(broken)
    try:
        reply = await send(session, "Хочу оператора")
    finally:
        escalation.unsubscribe(broken)

    assert reply is not None
    assert reply.escalated

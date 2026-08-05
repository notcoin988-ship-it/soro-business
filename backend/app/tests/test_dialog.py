"""Тесты пути одного сообщения (раздел 4 ТЗ).

Ответ пока эхо, но маршрут настоящий, и проверяется именно он: как
заводятся контакт и идентичность, почему диалог один на все каналы, что
попадает в `text`, а что в `text_masked`, и когда бот обязан замолчать.

Когда на шестом шаге появятся RAG и модель, эти тесты не должны
измениться — если изменятся, значит сломали маршрут, а не ответ.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.dialog import ECHO_TEMPLATE, handle_incoming, wants_operator
from app.models import ChannelIdentity, Contact, Conversation, Message

WS = "test-ws"
CARD = "5058123456789012"


async def count(session, model, workspace, *extra):
    """Счётчик строк ТОЛЬКО своего воркспейса.

    Считать по всей таблице нельзя: база общая с разработкой, и данные,
    оставленные руками через scripts/chat.py, ломали бы тесты. Тест обязан
    зависеть только от того, что сделал он сам.
    """
    return await session.scalar(
        select(func.count())
        .select_from(model)
        .where(model.workspace_id == workspace.id, *extra)
    )


async def send(session, text: str, *, channel="telegram", external_id="tg-1"):
    return await handle_incoming(
        session,
        channel=channel,
        external_id=external_id,
        text=text,
        workspace_slug=WS,
    )


# ---------------------------------------------------------------------------
# контакт и идентичность
# ---------------------------------------------------------------------------


async def test_first_message_creates_contact_and_identity(session, workspace):
    await send(session, "Салом!")

    contacts = await count(session, Contact, workspace)
    identities = await count(session, ChannelIdentity, workspace)
    assert contacts == 1
    assert identities == 1


async def test_same_sender_reuses_contact(session, workspace):
    """Второе сообщение того же человека не должно плодить контакты."""
    await send(session, "Салом!")
    await send(session, "Фоиз чанд аст?")

    contacts = await count(session, Contact, workspace)
    assert contacts == 1


async def test_different_channels_are_different_contacts_until_linked(
    session, workspace
):
    """Пока контакты не склеены, telegram и widget — разные люди.

    Склейка делается либо токеном из виджета (`/start <link_token>`), либо
    руками оператора. Автоматики по совпадению нет и быть не должно: два
    разных человека легко пишут с одного офисного номера.
    """
    await send(session, "Салом!", channel="telegram", external_id="tg-1")
    await send(session, "Салом!", channel="widget", external_id="w-1")

    contacts = await count(session, Contact, workspace)
    assert contacts == 2


# ---------------------------------------------------------------------------
# диалог
# ---------------------------------------------------------------------------


async def test_one_conversation_per_contact(session, workspace):
    first = await send(session, "Салом!")
    second = await send(session, "Боз як савол")

    assert first.conversation_id == second.conversation_id
    conversations = await count(session, Conversation, workspace)
    assert conversations == 1


async def test_messages_are_stored_both_sides(session, workspace):
    await send(session, "Фоизи амонат чанд аст?")

    roles = (
        await session.scalars(
            select(Message.role)
            .where(Message.workspace_id == workspace.id)
            .order_by(Message.id)
        )
    ).all()
    assert roles == ["user", "assistant"]


# ---------------------------------------------------------------------------
# ПДн: главный инвариант проекта
# ---------------------------------------------------------------------------


async def test_original_in_text_mask_in_text_masked(session, workspace):
    """В `text` оригинал — оператор должен видеть настоящий номер.
    В `text_masked` маска — это то, что уйдёт в модель.
    """
    await send(session, f"Корти ман {CARD} кор намекунад")

    incoming = await session.scalar(
        select(Message)
        .where(Message.workspace_id == workspace.id, Message.role == "user")
        .order_by(Message.id)
    )
    assert CARD in incoming.text
    assert CARD not in incoming.text_masked
    assert "[CARD]" in incoming.text_masked


async def test_bot_answer_never_contains_raw_pii(session, workspace):
    """Эхо строится из `text_masked`, а не из оригинала.

    Это репетиция того, что будет с моделью: наружу уходит только маска.
    """
    reply = await send(session, f"Корти ман {CARD}")

    assert CARD not in reply.text
    assert "[CARD]" in reply.text


async def test_echo_uses_masked_text(session, workspace):
    reply = await send(session, "Салом")
    assert reply.text == ECHO_TEMPLATE.format(text="Салом")


# ---------------------------------------------------------------------------
# просьба про оператора
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["Оператор дихед", "Хочу поговорить с человеком", "Одам лозим", "мутахассис"],
)
def test_operator_trigger_matches(text):
    assert wants_operator(text)


@pytest.mark.parametrize(
    "text", ["Фоизи амонат чанд аст?", "Кредит мехоҳам", "Салом!"],
)
def test_operator_trigger_does_not_overreach(text):
    assert not wants_operator(text)


async def test_operator_request_switches_conversation(session, workspace):
    reply = await send(session, "Оператор лозим")

    assert reply.escalated is True
    conversation = await session.scalar(
        select(Conversation).where(Conversation.workspace_id == workspace.id)
    )
    assert conversation.status == "operator"


async def test_bot_is_silent_while_operator_works(session, workspace):
    """Бот не перебивает живого человека.

    Сообщение при этом обязано сохраниться — иначе оператор не увидит, что
    ему написали, пока он печатал ответ.
    """
    await send(session, "Оператор лозим")
    reply = await send(session, "Ало, ҳастед?")

    assert reply is None
    user_messages = await count(session, Message, workspace, Message.role == "user")
    assert user_messages == 2


# ---------------------------------------------------------------------------
# телеметрия
# ---------------------------------------------------------------------------


async def test_latency_is_recorded(session, workspace):
    """Латентность нужна экрану 07 и нормативу «< 6 сек» на приёмке."""
    reply = await send(session, "Салом")

    answer = await session.scalar(
        select(Message)
        .where(Message.workspace_id == workspace.id, Message.role == "assistant")
        .order_by(Message.id.desc())
    )
    assert answer.latency_ms is not None
    assert answer.latency_ms == reply.latency_ms


async def test_unknown_workspace_is_an_error(session):
    """Молча отвечать от имени несуществующего банка нельзя."""
    with pytest.raises(LookupError):
        await handle_incoming(
            session,
            channel="telegram",
            external_id="tg-1",
            text="Салом",
            workspace_slug="нет-такого",
        )

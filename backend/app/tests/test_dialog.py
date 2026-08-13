"""Тесты пути одного сообщения (раздел 4 ТЗ).

Проверяется маршрут, а не ответ: как заводятся контакт и идентичность,
почему диалог один на все каналы, что попадает в `text`, а что в
`text_masked`, и когда бот обязан замолчать.

База знаний у тестового воркспейса пустая, поэтому поиск честно не
находит ответа и бот уводит клиента к оператору. Это и нужно: маршрут
проверяется без зависимости от содержимого базы и от модели. Сам ответ
модели — в `test_llm.py`, связка поиска с моделью — в `test_dialog_rag.py`.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.dialog import NO_ANSWER_REPLY, handle_incoming, wants_operator
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
    """Второе сообщение продолжает тот же диалог, а не заводит новый.

    Проверяем по базе, а не по ответам: на пустой базе знаний первый же
    вопрос уходит оператору, и на второй бот молчит (возвращает None) —
    это правильное поведение, диалогом уже занимается человек.
    """
    await send(session, "Салом!")
    await send(session, "Боз як савол")

    assert await count(session, Conversation, workspace) == 1
    assert await count(session, Contact, workspace) == 1


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
    """Наружу номер карты не уходит ни при каком ответе."""
    reply = await send(session, f"Корти ман {CARD}")

    assert CARD not in reply.text


# ---------------------------------------------------------------------------
# вежливость без вопроса
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text", ["салом", "Салом!", "  САЛОМ  ", "привет", "Здравствуйте!", "hi"]
)
async def test_greeting_is_answered_not_escalated(session, workspace, text):
    """«Салом» не должен будить оператора.

    До этой правки приветствие уходило в поиск, ничего не находило и
    заводило карточку в инбоксе — каждое «привет» отрывало человека от
    работы.
    """
    reply = await send(session, text)

    assert reply is not None
    assert not reply.escalated
    assert reply.reason is None
    assert "Soro" in reply.text


@pytest.mark.parametrize("text", ["раҳмат", "Спасибо!", "ташаккур"])
async def test_thanks_is_answered(session, workspace, text):
    reply = await send(session, text)
    assert not reply.escalated


async def test_farewell_is_answered(session, workspace):
    reply = await send(session, "хайр")
    assert not reply.escalated


async def test_question_with_greeting_is_not_smalltalk(session, workspace):
    """Сторож: «Салом! Фоизи амонат чанд аст?» — это ВОПРОС.

    Сравнение идёт по всему сообщению целиком именно поэтому: проверка по
    вхождению перехватывала бы каждый вежливый вопрос и бот отвечал бы на
    них приветствием.
    """
    reply = await send(session, "Салом! Фоизи амонат чанд аст?")

    assert "Soro" not in reply.text
    # база знаний тестового воркспейса пуста, поэтому честная эскалация
    assert reply.escalated


async def test_greeting_can_be_overridden_by_workspace(session, workspace):
    """Приветствие берётся из настроек воркспейса (раздел 7.1)."""
    workspace.settings = {"greeting": "Хуш омадед ба бонки мо!"}
    await session.flush()

    reply = await send(session, "салом")
    assert reply.text == "Хуш омадед ба бонки мо!"


async def test_empty_knowledge_base_escalates(session, workspace):
    """База знаний пуста — отвечать нечем, и бот честно зовёт оператора.

    Ровно этого требует раздел 3.2: лучше лишняя эскалация, чем выдумка.
    """
    reply = await send(session, "Фоизи амонат чанд аст?")

    assert reply.text == NO_ANSWER_REPLY
    assert reply.escalated
    assert reply.reason == "no_answer"
    assert reply.chunks_used == []


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


async def test_conversation_marks_itself_fresh(session, workspace):
    """`last_msg_at` двигается на каждом сообщении.

    Найдено живым прогоном: в базе лежал диалог с отметкой недельной
    давности, в котором только что переписывались. По этой колонке трое
    выбирают «последний незакрытый диалог» — ядро, склейка контактов и
    история виджета, — и у контакта с двумя открытыми диалогами (а два их
    становится ровно после склейки) последним оказался бы не тот.
    """
    await send(session, "Салом")
    conversation = await session.scalar(
        select(Conversation).where(Conversation.workspace_id == workspace.id)
    )
    started = conversation.last_msg_at

    await send(session, "Раҳмат")
    await session.refresh(conversation)

    assert conversation.last_msg_at > started


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

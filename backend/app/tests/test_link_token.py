"""Склейка контактов по `link_token` (разделы 5.1 и 7.2 ТЗ).

Критерий готовности раздела 5 требует этот тест дословно: завести
widget-идентичность, дёрнуть `/start` с токеном, проверить, что обе
идентичности указывают на один контакт и что история видна из обоих
каналов. Это и есть омниканальность из экрана 04: без склейки виджет и
Telegram — два разных человека, и «продолжим с того же места» превращается
в «здравствуйте, чем помочь».

БАЗА НАСТОЯЩАЯ. Половина склейки — это UPDATE по трём таблицам и выбор
открытого диалога; на моках такое не проверяется.

REDIS ПОДДЕЛЬНЫЙ. Он здесь хранилище на две операции, и поднимать его
ради `setex`/`getdel` незачем — зато подделка позволяет проверить то, что
на живом Redis проверяется плохо: истёкший токен.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from aiogram.types import Message as TgMessage
from sqlalchemy import func, select

from app.channels import telegram, widget
from app.core import dialog
from app.core.linking import merge_contacts
from app.models import ChannelIdentity, Contact, Conversation, Message

TG_USER = 777
WIDGET_UUID = "b6f0c7a2-widget-uuid"


class FakeRedis:
    """Redis на словаре: только `setex` и `getdel`, только их семантика.

    TTL не тикает — вместо ожидания пятнадцати минут тест стирает ключ
    сам. Проверяем поведение при истёкшем токене, а не работу часов.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def setex(self, key: str, ttl: int, value: str) -> None:
        assert ttl > 0, "токен без срока жизни живёт вечно"
        self.store[key] = value

    def getdel(self, key: str):
        return self.store.pop(key, None)


@pytest.fixture
def redis(monkeypatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(widget, "_redis", lambda: fake)
    return fake


@pytest.fixture
def tg_db(session, monkeypatch):
    """Канал ходит в базу через свой `SessionLocal` — подменяем его на
    тестовую сессию, иначе склейка запишется мимо откатываемой транзакции
    и останется в базе разработки."""

    @asynccontextmanager
    async def factory():
        yield session

    monkeypatch.setattr(telegram, "SessionLocal", factory)


def start_message(token: str) -> TgMessage:
    return TgMessage.model_validate(
        {
            "message_id": 1,
            "date": int(datetime.now(tz=timezone.utc).timestamp()),
            "chat": {"id": TG_USER, "type": "private"},
            "from": {"id": TG_USER, "is_bot": False, "first_name": "Далер"},
            "text": f"/start {token}",
        }
    )


async def widget_client(session, workspace, text: str = "Фоизи амонат чанд аст?"):
    """Клиент, который уже поговорил в виджете и сейчас нажмёт «Продолжить
    в Telegram». Возвращает его идентичность."""
    identity = await dialog.resolve_identity(
        session, workspace.id, "widget", WIDGET_UUID
    )
    conversation = await dialog.resolve_conversation(
        session, workspace.id, identity.contact_id
    )
    await dialog.save_message(
        session,
        conversation=conversation,
        channel="widget",
        role="user",
        text=text,
    )
    return identity


# ---------------------------------------------------------------------------
# сквозной путь: виджет выдал токен — Telegram его принёс
# ---------------------------------------------------------------------------


async def test_start_with_token_makes_one_contact(session, workspace, redis, tg_db):
    """Требование критерия готовности: обе идентичности — один контакт."""
    identity = await widget_client(session, workspace)
    token = widget.issue_token(identity.id)

    assert await telegram.link_from_widget(token, start_message(token)) is True

    identities = (
        await session.scalars(
            select(ChannelIdentity).where(
                ChannelIdentity.workspace_id == workspace.id
            )
        )
    ).all()
    assert {i.channel for i in identities} == {"widget", "telegram"}
    assert len({i.contact_id for i in identities}) == 1, "контакты не склеились"


async def test_history_is_visible_from_both_channels(
    session, workspace, redis, tg_db
):
    """Оператор и бот должны видеть один разговор, а не два обрывка.

    Проверяем через `resolve_conversation` — тот же путь, которым ядро
    ищет диалог на каждое входящее.
    """
    identity = await widget_client(session, workspace, "Фоизи амонат чанд аст?")
    token = widget.issue_token(identity.id)
    await telegram.link_from_widget(token, start_message(token))

    # Фильтр по воркспейсу обязателен: база разработки живая, и в ней
    # лежат telegram-идентичности прошлых прогонов и ручных проверок.
    telegram_identity = await session.scalar(
        select(ChannelIdentity).where(
            ChannelIdentity.workspace_id == workspace.id,
            ChannelIdentity.channel == "telegram",
        )
    )
    conversation = await dialog.resolve_conversation(
        session, workspace.id, telegram_identity.contact_id
    )
    await dialog.save_message(
        session,
        conversation=conversation,
        channel="telegram",
        role="user",
        text="Ва мӯҳлаташ чанд сол?",
    )

    texts = (
        await session.scalars(
            select(Message.text)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.id)
        )
    ).all()
    assert texts == ["Фоизи амонат чанд аст?", "Ва мӯҳлаташ чанд сол?"]


async def test_token_burns_after_first_use(session, workspace, redis, tg_db):
    """Ссылка `t.me/bot?start=<токен>` оседает в истории браузера и в
    пересланных сообщениях. Второй переход по ней не должен давать доступ
    к чужой переписке."""
    identity = await widget_client(session, workspace)
    token = widget.issue_token(identity.id)

    await telegram.link_from_widget(token, start_message(token))

    assert await telegram.link_from_widget(token, start_message(token)) is False


async def test_expired_token_leaves_contacts_apart(session, workspace, redis, tg_db):
    identity = await widget_client(session, workspace)
    token = widget.issue_token(identity.id)
    redis.store.clear()  # пятнадцать минут прошли

    assert await telegram.link_from_widget(token, start_message(token)) is False

    contacts = await session.scalar(
        select(func.count(Contact.id)).where(Contact.workspace_id == workspace.id)
    )
    assert contacts == 1, "склейки не было, а лишний контакт завёлся"


async def test_greeting_survives_broken_redis(session, workspace, monkeypatch, tg_db):
    """Redis лежит — поздороваться бот обязан всё равно."""

    def explode():
        raise ConnectionError("redis недоступен")

    monkeypatch.setattr(widget, "_redis", explode)

    assert await telegram.answer_for(start_message("abc")) == telegram.GREETING


# ---------------------------------------------------------------------------
# сама склейка
# ---------------------------------------------------------------------------


async def test_older_contact_wins(session, workspace):
    """Побеждает контакт, которого завели раньше: его id мог уже уехать в
    аудит-лог, и переписывать историю дороже, чем перевесить новичка."""
    old = Contact(workspace_id=workspace.id, display_name="Далер")
    session.add(old)
    await session.flush()
    new = Contact(workspace_id=workspace.id)
    session.add(new)
    await session.flush()

    # Порядок аргументов не решает — правило одно и живёт в merge_contacts.
    winner = await merge_contacts(session, keep=new, drop=old)

    assert winner.id == old.id
    assert new.merged_into == old.id
    assert old.merged_into is None


async def test_messages_end_up_in_one_conversation(session, workspace):
    """У обоих контактов был открытый диалог — после склейки открытый один,
    и в нём вся переписка."""
    contacts = []
    for _ in range(2):
        contact = Contact(workspace_id=workspace.id)
        session.add(contact)
        await session.flush()
        contacts.append(contact)

    for contact, text in zip(contacts, ["первый", "второй"]):
        conversation = await dialog.resolve_conversation(
            session, workspace.id, contact.id
        )
        await dialog.save_message(
            session,
            conversation=conversation,
            channel="widget",
            role="user",
            text=text,
        )

    keep = await merge_contacts(session, keep=contacts[0], drop=contacts[1])

    open_conversations = (
        await session.scalars(
            select(Conversation).where(
                Conversation.contact_id == keep.id,
                Conversation.status != "closed",
            )
        )
    ).all()
    assert len(open_conversations) == 1

    texts = (
        await session.scalars(
            select(Message.text)
            .where(Message.conversation_id == open_conversations[0].id)
            .order_by(Message.id)
        )
    ).all()
    assert texts == ["первый", "второй"]


async def test_merging_a_contact_with_itself_changes_nothing(session, workspace):
    contact = Contact(workspace_id=workspace.id)
    session.add(contact)
    await session.flush()

    assert (await merge_contacts(session, keep=contact, drop=contact)).id == contact.id
    assert contact.merged_into is None

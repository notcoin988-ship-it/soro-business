"""Ядро: обработка входящего сообщения (раздел 4 ТЗ, «путь одного сообщения»).

Единственная точка, через которую проходит сообщение из любого канала.
Каналы не думают: Telegram, виджет и WhatsApp отличаются только форматом
входа и выхода, а что ответить — решается здесь. Иначе логика расползётся
по трём файлам, и в WhatsApp бот начнёт вести себя не так, как в Telegram.

СОСТОЯНИЕ: неделя 1 — каркас без RAG. Бот отвечает эхом, но весь путь
сообщения уже настоящий: контакт, идентичность, диалог, маскирование ПДн,
запись в `messages`. Когда появятся `core.rag` и `core.llm`, меняется
только шаг 6, всё остальное уже работает и покрыто тестами.

ПОРЯДОК ШАГОВ ОБЯЗАТЕЛЕН:

 1. контакт и идентичность по каналу (`channel` + `external_id`);
 2. диалог — общий для всех каналов контакта, а не для канала;
 3. маскирование ПДн: в `text` оригинал, в `text_masked` маска;
 4. диалог у оператора — бот молчит, сообщение просто ложится в инбокс;
 5. просьба про оператора → эскалация;
 6. ответ (пока эхо, дальше RAG + модель);
 7. запись ответа с телеметрией.

Шаг 3 стоит третьим не случайно: всё, что ниже, работает уже с
`text_masked`, и оригинал в модель не попадает никогда.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.pii import mask
from app.models import ChannelIdentity, Contact, Conversation, Message, Workspace

# Триггер «позовите человека» из раздела 8.1: оператор, одам, человек,
# мутахассис. Компилируется один раз — проверяется на каждом сообщении.
OPERATOR_RE = re.compile(settings.OPERATOR_REQUEST_RE, re.I)

# Пока нет модели, бот отвечает эхом. Формулировка временная и намеренно
# заметная: если она доживёт до демо, это будет видно сразу.
ECHO_TEMPLATE = "Шумо навиштед: «{text}». Ҷустуҷӯ ҳанӯз пайваст нашудааст."
OPERATOR_REPLY = "Ҳозир мутахассисро пайваст мекунам."


@dataclass
class Reply:
    """Что ядро вернуло каналу."""

    text: str
    conversation_id: int
    message_id: int
    latency_ms: int
    escalated: bool = False


async def get_workspace(session: AsyncSession, slug: str | None = None) -> Workspace:
    """Воркспейс по slug. В этой версии он один — из `.env`."""
    slug = slug or settings.WORKSPACE_DEFAULT_SLUG
    workspace = await session.scalar(select(Workspace).where(Workspace.slug == slug))
    if workspace is None:
        raise LookupError(f"воркспейс {slug!r} не заведён")
    return workspace


async def resolve_identity(
    session: AsyncSession,
    workspace_id: int,
    channel: str,
    external_id: str,
    display_name: str | None = None,
) -> ChannelIdentity:
    """Найти идентичность канала или завести вместе с новым контактом.

    Уникальность — по тройке (воркспейс, канал, внешний id): один и тот же
    telegram id в двух воркспейсах это два разных человека.
    """
    identity = await session.scalar(
        select(ChannelIdentity).where(
            ChannelIdentity.workspace_id == workspace_id,
            ChannelIdentity.channel == channel,
            ChannelIdentity.external_id == str(external_id),
        )
    )
    if identity is not None:
        return identity

    contact = Contact(workspace_id=workspace_id, display_name=display_name)
    session.add(contact)
    await session.flush()

    identity = ChannelIdentity(
        workspace_id=workspace_id,
        contact_id=contact.id,
        channel=channel,
        external_id=str(external_id),
    )
    session.add(identity)
    await session.flush()
    return identity


async def resolve_conversation(
    session: AsyncSession, workspace_id: int, contact_id: int
) -> Conversation:
    """Открытый диалог контакта — ОДИН на все каналы.

    Здесь и живёт омниканальность: клиент пишет в Telegram, продолжает в
    виджете, а `conversation` тот же. Привяжи диалог к каналу — и сценарий
    экрана 04 развалится.
    """
    conversation = await session.scalar(
        select(Conversation)
        .where(
            Conversation.workspace_id == workspace_id,
            Conversation.contact_id == contact_id,
            Conversation.status != "closed",
        )
        .order_by(Conversation.last_msg_at.desc())
    )
    if conversation is not None:
        return conversation

    conversation = Conversation(workspace_id=workspace_id, contact_id=contact_id)
    session.add(conversation)
    await session.flush()
    return conversation


def wants_operator(text: str) -> bool:
    return bool(OPERATOR_RE.search(text or ""))


async def save_message(
    session: AsyncSession,
    *,
    conversation: Conversation,
    channel: str,
    role: str,
    text: str,
    latency_ms: int | None = None,
    chunks_used: list[int] | None = None,
) -> Message:
    """Записать сообщение. Маскирование здесь, а не в канале.

    `text` — оригинал, `text_masked` — то, что уйдёт в модель. Перепутать
    эти две колонки — самая дорогая ошибка проекта: либо оператор увидит
    маску вместо номера, либо номер клиента уйдёт наружу.
    """
    message = Message(
        workspace_id=conversation.workspace_id,
        conversation_id=conversation.id,
        channel=channel,
        role=role,
        text=text,
        text_masked=mask(text),
        latency_ms=latency_ms,
        chunks_used=chunks_used or [],
    )
    session.add(message)
    await session.flush()
    return message


async def handle_incoming(
    session: AsyncSession,
    *,
    channel: str,
    external_id: str,
    text: str,
    display_name: str | None = None,
    workspace_slug: str | None = None,
) -> Reply | None:
    """Путь одного сообщения. `None` — бот отвечать не должен.

    `None` возвращается, когда диалогом занимается оператор: бот не
    перебивает живого человека, сообщение просто ложится в инбокс.
    """
    started = time.monotonic()

    workspace = await get_workspace(session, workspace_slug)
    identity = await resolve_identity(
        session, workspace.id, channel, external_id, display_name
    )
    conversation = await resolve_conversation(
        session, workspace.id, identity.contact_id
    )

    incoming = await save_message(
        session,
        conversation=conversation,
        channel=channel,
        role="user",
        text=text,
    )

    # Диалог уже у оператора — бот молчит.
    if conversation.status == "operator":
        await session.commit()
        return None

    # Клиент просит человека. Полная эскалация появится вместе с
    # core.escalation; пока переводим статус, чтобы бот замолчал.
    if wants_operator(text):
        conversation.status = "operator"
        latency_ms = int((time.monotonic() - started) * 1000)
        outgoing = await save_message(
            session,
            conversation=conversation,
            channel=channel,
            role="assistant",
            text=OPERATOR_REPLY,
            latency_ms=latency_ms,
        )
        await session.commit()
        return Reply(
            text=OPERATOR_REPLY,
            conversation_id=conversation.id,
            message_id=outgoing.id,
            latency_ms=latency_ms,
            escalated=True,
        )

    # Здесь будут RAG и модель. Пока эхо — но по тому же маршруту:
    # наружу уходит text_masked, а не оригинал.
    answer = ECHO_TEMPLATE.format(text=incoming.text_masked)
    latency_ms = int((time.monotonic() - started) * 1000)

    outgoing = await save_message(
        session,
        conversation=conversation,
        channel=channel,
        role="assistant",
        text=answer,
        latency_ms=latency_ms,
    )
    await session.commit()

    return Reply(
        text=answer,
        conversation_id=conversation.id,
        message_id=outgoing.id,
        latency_ms=latency_ms,
    )

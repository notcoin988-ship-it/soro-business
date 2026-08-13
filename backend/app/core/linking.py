"""Склейка контактов: два канала — один человек (раздел 5.1 ТЗ).

ЗАЧЕМ. Далер спросил про вклад в Telegram по дороге домой, вечером открыл
сайт и продолжил в виджете. Для базы это два контакта: telegram-id и
cookie-uuid между собой ничем не связаны. Пока их не связать, виджет
начнёт разговор заново, а оператор увидит два диалога вместо одного — то
есть ровно то, что демо на экране 04 обещает не делать.

ПОЧЕМУ ТОЛЬКО ПО ТОКЕНУ. Соблазн склеивать по совпадению — имени, номера,
времени — надо давить: два разных человека легко пишут с одного офисного
номера, а склеенные контакты потом не разделить, и один клиент увидит
переписку другого. Связываем ровно тогда, когда клиент сам нажал
«Продолжить в Telegram» и принёс одноразовый токен, либо когда контакты
свёл оператор руками.

ЧТО ЗНАЧИТ «СКЛЕИТЬ». Побеждает контакт, которого завели раньше: он
старше по разговору, и его id уже мог уехать в аудит-лог. Проигравший не
удаляется, а помечается `merged_into` — ссылки на него из старых записей
должны оставаться разрешимыми. Идентичности переезжают на победителя,
сообщения из открытых диалогов проигравшего переносятся в его открытый
диалог, опустевшие диалоги закрываются.

ПОЧЕМУ СООБЩЕНИЯ ПЕРЕЕЗЖАЮТ, А НЕ ДИАЛОГИ. `resolve_conversation` берёт
последний незакрытый диалог контакта. Если просто перевесить диалоги на
победителя, у него станет два открытых — и половина переписки останется в
том, который больше никто не выберет. Оператор в инбоксе увидит обрывок.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChannelIdentity, Contact, Conversation, Message

log = logging.getLogger(__name__)


async def merge_contacts(
    session: AsyncSession, *, keep: Contact, drop: Contact
) -> Contact:
    """Слить `drop` в `keep`. Возвращает контакт-победитель.

    Порядок аргументов не решает, кто победит: старшинство считается по
    `first_seen`. Вызывающему не нужно помнить правило, а правило живёт в
    одном месте.
    """
    if keep.id == drop.id:
        return keep

    # Кто старше, спрашиваем у базы, а не у объектов: `first_seen` ставит
    # сервер, и в свежесозданном контакте этого значения ещё нет — попытка
    # прочитать его из объекта в async-сессии кончится подгрузкой посреди
    # чужого greenlet. Ничья по времени (оба контакта завели в одной
    # транзакции) разбивается меньшим id — он тоже про «раньше».
    order = (
        await session.scalars(
            select(Contact.id)
            .where(Contact.id.in_([keep.id, drop.id]))
            .order_by(Contact.first_seen.asc(), Contact.id.asc())
        )
    ).all()
    if order and order[0] != keep.id:
        keep, drop = drop, keep

    await session.execute(
        update(ChannelIdentity)
        .where(ChannelIdentity.contact_id == drop.id)
        .values(contact_id=keep.id)
    )

    target = await _open_conversation(session, keep.id)
    for conversation in await _open_conversations(session, drop.id):
        if target is None:
            # У победителя открытого диалога нет — перекладывать сообщения
            # некуда и незачем: этот диалог и станет общим.
            target = conversation
            continue

        await session.execute(
            update(Message)
            .where(Message.conversation_id == conversation.id)
            .values(conversation_id=target.id)
        )
        conversation.status = "closed"
        conversation.closed_at = datetime.now(tz=timezone.utc)

    # Закрытые диалоги проигравшего тоже переезжают: инбокс показывает
    # историю по контакту, и оставить её на контакте-призраке значит
    # потерять всё, что было до склейки.
    await session.execute(
        update(Conversation)
        .where(Conversation.contact_id == drop.id)
        .values(contact_id=keep.id)
    )

    # Имя лучше то, которое человек написал сам: в виджете его нет вообще,
    # в Telegram оно из профиля.
    if not keep.display_name and drop.display_name:
        keep.display_name = drop.display_name

    drop.merged_into = keep.id
    await session.flush()

    log.info("контакты склеены: %s ← %s", keep.id, drop.id)
    return keep


async def link_identities(
    session: AsyncSession, *, first: ChannelIdentity, second: ChannelIdentity
) -> Contact:
    """Связать две идентичности одного человека.

    Точка входа для `/start <link_token>`: на входе идентичность виджета,
    выдавшая токен, и идентичность Telegram, принёсшая его обратно.
    """
    if first.contact_id == second.contact_id:
        return await session.get(Contact, first.contact_id)

    keep = await session.get(Contact, first.contact_id)
    drop = await session.get(Contact, second.contact_id)
    return await merge_contacts(session, keep=keep, drop=drop)


async def _open_conversation(
    session: AsyncSession, contact_id: int
) -> Conversation | None:
    """Тот же выбор, что делает `dialog.resolve_conversation`."""
    return await session.scalar(
        select(Conversation)
        .where(
            Conversation.contact_id == contact_id,
            Conversation.status != "closed",
        )
        .order_by(Conversation.last_msg_at.desc())
    )


async def _open_conversations(
    session: AsyncSession, contact_id: int
) -> list[Conversation]:
    rows = await session.scalars(
        select(Conversation)
        .where(
            Conversation.contact_id == contact_id,
            Conversation.status != "closed",
        )
        .order_by(Conversation.last_msg_at)
    )
    return list(rows)

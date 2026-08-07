"""Ядро: обработка входящего сообщения (раздел 4 ТЗ, «путь одного сообщения»).

Единственная точка, через которую проходит сообщение из любого канала.
Каналы не думают: Telegram, виджет и WhatsApp отличаются только форматом
входа и выхода, а что ответить — решается здесь. Иначе логика расползётся
по трём файлам, и в WhatsApp бот начнёт вести себя не так, как в Telegram.

ПОРЯДОК ШАГОВ ОБЯЗАТЕЛЕН:

 1. контакт и идентичность по каналу (`channel` + `external_id`);
 2. диалог — общий для всех каналов контакта, а не для канала;
 3. маскирование ПДн: в `text` оригинал, в `text_masked` маска;
 4. диалог у оператора — бот молчит, сообщение просто ложится в инбокс;
 5. просьба про оператора → эскалация;
 6. ответ: поиск по базе знаний, затем модель (см. `_answer`);
 7. запись ответа с телеметрией и `chunks_used`.

Шаг 3 стоит третьим не случайно: всё, что ниже, работает уже с
`text_masked`, и оригинал в модель не попадает никогда.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import audit, escalation, llm, policy, rag
from app.core.pii import mask
from app.models import ChannelIdentity, Contact, Conversation, Message, Workspace

log = logging.getLogger(__name__)

# Триггер «позовите человека» из раздела 8.1: оператор, одам, человек,
# мутахассис. Компилируется один раз — проверяется на каждом сообщении.
OPERATOR_RE = re.compile(settings.OPERATOR_REQUEST_RE, re.I)

OPERATOR_REPLY = "Ҳозир мутахассисро пайваст мекунам."

# --- вежливые реплики без вопроса ------------------------------------------
#
# «Салом» уходил в поиск, ничего там не находил и заводил карточку в
# инбоксе: каждое приветствие клиента отрывало оператора от работы. Это
# не поиск и не эскалация, это разговорная вежливость, и отвечать на неё
# надо сразу.
#
# Сравниваем сообщение ЦЕЛИКОМ, а не по вхождению: «Салом! Фоизи амонат
# чанд аст?» — обычный вопрос, и перехватывать его нельзя.

GREETINGS = frozenset({
    "салом", "салом алейкум", "ассалом", "ассалому алейкум",
    "ассалому алайкум", "салам", "салом бародар",
    "привет", "приветствую", "здравствуйте", "здравствуй", "здрасте",
    "добрый день", "добрый вечер", "доброе утро",
    "hello", "hi", "hey", "start",
})
THANKS = frozenset({
    "раҳмат", "рахмат", "ташаккур", "сипос", "раҳмат калон",
    "спасибо", "спасибо большое", "благодарю", "мерси",
})
FAREWELL = frozenset({
    "хайр", "то дидор", "хуш омадед",
    "пока", "до свидания", "всего доброго", "спокойной ночи",
})

# Знаки препинания и эмодзи убираем: «Салом!!!» и «салом 😊» — то же самое.
NOT_WORD_RE = re.compile(r"[^\w\s]|_", re.UNICODE)

GREETING_REPLY = (
    "Салом! Ман Soro, ёрдамчии бонк. Дар бораи амонатҳо, қарзҳо, кортҳо ва "
    "тарифҳо пурсед — ҷавоб медиҳам.\n"
    "Здравствуйте! Я Soro, помощник банка. Спросите про вклады, кредиты, "
    "карты и тарифы — отвечу."
)
THANKS_REPLY = (
    "Хоҳиш мекунам! Боз саволе ҳаст?\nПожалуйста! Ещё вопросы есть?"
)
FAREWELL_REPLY = "Хайр, рӯзи хуш!\nВсего доброго!"


def smalltalk_reply(text: str, workspace: Workspace) -> str | None:
    """Ответ на вежливость без вопроса. `None` — это не вежливость.

    Приветствие можно переопределить в `workspace.settings['greeting']` —
    так требует раздел 7.1 для `/start`, и здесь берём оттуда же, чтобы у
    бота не было двух разных приветствий.
    """
    words = NOT_WORD_RE.sub(" ", (text or "").lower())
    words = " ".join(words.split())
    if not words:
        return None

    if words in GREETINGS:
        return (workspace.settings or {}).get("greeting") or GREETING_REPLY
    if words in THANKS:
        return THANKS_REPLY
    if words in FAREWELL:
        return FAREWELL_REPLY
    return None

# Ответа в базе знаний нет — говорим об этом сами, не тратя вызов модели.
# Двуязычно, потому что язык вопроса здесь ещё не определён: определять его
# для одной фразы — лишний код, а на демо клиент пишет и так и так.
NO_ANSWER_REPLY = (
    "Ин маълумот дар ҳуҷҷатҳои бонк нест — ҳозир мутахассисро пайваст мекунам.\n"
    "Этой информации нет в документах банка — соединяю со специалистом."
)

# Модель недоступна. Отдельная формулировка от NO_ANSWER_REPLY: там ответа
# нет в документах, здесь мы просто не смогли спросить, и оператору важно
# видеть разницу в причине эскалации.
LLM_DOWN_REPLY = (
    "Ҳозир ҷавоб дода наметавонам, мутахассисро пайваст мекунам.\n"
    "Сейчас не могу ответить, соединяю со специалистом."
)


@dataclass
class Reply:
    """Что ядро вернуло каналу."""

    text: str
    conversation_id: int
    message_id: int
    latency_ms: int
    escalated: bool = False
    # причина эскалации для инбокса: 'no_answer' | 'pii_topic' |
    # 'user_request' | 'llm_unavailable'
    reason: str | None = None
    # фрагменты в порядке ссылок [1], [2] — из них канал строит бейджи в
    # консоли и футер «Манбаъ: …» в Telegram (раздел 6.6)
    chunks_used: list[int] = field(default_factory=list)


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
    workspace: Workspace | None = None,
) -> Message:
    """Записать сообщение. Маскирование здесь, а не в канале.

    `text` — оригинал, `text_masked` — то, что уйдёт в модель. Перепутать
    эти две колонки — самая дорогая ошибка проекта: либо оператор увидит
    маску вместо номера, либо номер клиента уйдёт наружу.

    `workspace` нужен только чтобы спросить контур безопасности. Без него
    маскируем — умолчание всегда в пользу защиты.
    """
    masking = workspace is None or policy.enabled(workspace, policy.MASK_PII)
    message = Message(
        workspace_id=conversation.workspace_id,
        conversation_id=conversation.id,
        channel=channel,
        role=role,
        text=text,
        # Колонки местами не меняем никогда: слева оригинал, справа то,
        # что уйдёт в модель. Выключенный тумблер делает их одинаковыми,
        # а не переставляет.
        text_masked=mask(text) if masking else text,
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
        workspace=workspace,
    )

    # Диалог уже у оператора — бот молчит.
    if conversation.status == "operator":
        await session.commit()
        return None

    # Клиент просит человека — самая понятная из четырёх причин 8.1.
    if wants_operator(text):
        await escalation.escalate(
            session, conversation, escalation.REASON_USER_REQUEST
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        outgoing = await save_message(
            session,
            conversation=conversation,
            channel=channel,
            role="assistant",
            text=OPERATOR_REPLY,
            latency_ms=latency_ms,
            workspace=workspace,
        )
        await session.commit()
        return Reply(
            text=OPERATOR_REPLY,
            conversation_id=conversation.id,
            message_id=outgoing.id,
            latency_ms=latency_ms,
            escalated=True,
            reason=escalation.REASON_USER_REQUEST,
        )

    # Вежливость без вопроса: «салом», «раҳмат», «пока». Отвечаем сразу —
    # ни поиск, ни модель, ни оператор тут не нужны. Проверяем ПОСЛЕ
    # просьбы об операторе: «оператор» важнее приветствия.
    smalltalk = smalltalk_reply(text, workspace)
    if smalltalk is not None:
        latency_ms = int((time.monotonic() - started) * 1000)
        outgoing = await save_message(
            session,
            conversation=conversation,
            channel=channel,
            role="assistant",
            text=smalltalk,
            latency_ms=latency_ms,
            workspace=workspace,
        )
        await session.commit()
        return Reply(
            text=smalltalk,
            conversation_id=conversation.id,
            message_id=outgoing.id,
            latency_ms=latency_ms,
        )

    # RAG и модель. В поиск и в модель уходит text_masked, а не оригинал:
    # так номер карты клиента не попадёт ни в промпт, ни в логи провайдера.
    answer_text, chunks_used, needs_operator, reason = await _answer(
        session, workspace, incoming.text_masked
    )
    if needs_operator:
        # Причина `llm_unavailable` в CHECK не входит и схлопнется в
        # `no_answer` внутри модуля; наружу в Reply уходит подробная.
        await escalation.escalate(session, conversation, reason)

    latency_ms = int((time.monotonic() - started) * 1000)
    outgoing = await save_message(
        session,
        conversation=conversation,
        channel=channel,
        role="assistant",
        text=answer_text,
        latency_ms=latency_ms,
        chunks_used=chunks_used,
        workspace=workspace,
    )
    await session.commit()

    return Reply(
        text=answer_text,
        conversation_id=conversation.id,
        message_id=outgoing.id,
        latency_ms=latency_ms,
        escalated=needs_operator,
        reason=reason,
        chunks_used=chunks_used,
    )


async def _answer(
    session: AsyncSession, workspace: Workspace, question: str
) -> tuple[str, list[int], bool, str | None]:
    """Поиск → модель → текст. Возвращает (текст, chunks_used, эскалация, причина).

    Два фильтра подряд, и оба нужны:

    1. порог поиска (раздел 6.4) — если лучший фрагмент ниже
       `RAG_MIN_SCORE`, модель не зовём вовсе. Это экономит секунды на
       вопросах вроде «какой у меня баланс», где отвечать всё равно нечем;
    2. сама модель — правило 1 промпта велит ей поставить `[ESCALATE]`,
       если во фрагментах ответа нет. Порог грубый и пропускает похожие,
       но не подходящие фрагменты; модель видит текст и решает точнее.

    Прогон golden set показал, почему одного порога мало: при 0,65 нет ни
    одного выдуманного ответа, но 16 вопросов из 40 уходят оператору,
    хотя ответ на них есть и найден. Второй фильтр позволит когда-нибудь
    опустить порог, не начав выдумывать.
    """
    found = await rag.search(session, question, workspace.id)

    if not found.has_answer:
        # Причину уточняем и здесь, а не только по ответу модели: вопросы
        # вроде «почему у меня списали 90 сомони» порог отсекает раньше,
        # чем модель их увидит, и оператор получал бы «нет ответа» вместо
        # «клиент спрашивает про свои деньги». Разница в том, лезть ли
        # ему сразу в АБС.
        reason = (
            llm.REASON_PII_TOPIC
            if llm.pii_topic(question)
            else llm.REASON_NO_ANSWER
        )
        return NO_ANSWER_REPLY, [], True, reason

    try:
        result = await llm.answer(
            question, found.hits, bank_name=workspace.name
        )
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        # Модель недоступна — это не повод показать клиенту трейс. Уводим
        # к оператору и оставляем след в логе: на демо это первое, что
        # придётся проверять.
        log.warning("модель недоступна (%s): %s", type(exc).__name__, exc)
        return LLM_DOWN_REPLY, [], True, "llm_unavailable"

    text, chunks_used = result.text, result.chunks_used
    if not policy.enabled(workspace, policy.CITE_SOURCES):
        # Ссылки выключены в контуре безопасности: убираем и номера из
        # текста, и chunks_used — иначе канал нарисует бейджи, которых
        # в ответе уже нет.
        text = llm.strip_citations(text)
        chunks_used = []

    await audit.record(
        session,
        workspace,
        audit.EVENT_LLM_CALL,
        {
            # вопрос уже маскированный: сюда он приходит из text_masked
            "question": question,
            "chunk_ids": result.chunks_used,
            "latency_ms": result.latency_ms,
            "first_token_ms": result.first_token_ms,
            "model": settings.SORO_MODEL,
            "escalated": result.escalate,
        },
    )

    return text, chunks_used, result.escalate, result.reason

"""Передача диалога оператору (раздел 8.1 ТЗ).

ОТВЕТСТВЕННОСТЬ: перевести диалог в статус `operator`, создать запись в
`escalations` и разбудить инбокс (WebSocket + звук).

ПРИЧИНЫ (CHECK в DDL, других быть не может):
  `no_answer`      — поиск ничего не нашёл выше порога;
  `low_confidence` — нашёл, но модель не уверена;
  `user_request`   — клиент сам попросил человека (`OPERATOR_REQUEST_RE`);
  `pii_topic`      — вопрос про личные данные: баланс, списания, лимиты.

ПРИНЦИП ИЗ ТЗ: лишняя эскалация дешевле выдуманного ответа. При сомнении
эскалируем.

ВОЗВРАТ БОТУ: кнопка «Вернуть боту» в инбоксе ставит статус обратно в
`bot` и заполняет `resolved_at`.

ПОЧЕМУ ЗАПИСЬ, А НЕ ТОЛЬКО ФЛАГ. `conversations.status` отвечает на
вопрос «занят ли диалог сейчас». Строка в `escalations` отвечает на
«сколько раз за неделю бот сдавался, по каким причинам, кто разбирал и
как быстро» — на этом стоит весь экран 07, первый же запрос приложения Б
считает долю диалогов, закрытых БЕЗ эскалации.

ОТКЛОНЕНИЕ ОТ КОДА, НО НЕ ОТ ТЗ: `dialog.py` умеет ещё одну ситуацию —
модель недоступна. В списке причин ТЗ её нет, и расширять CHECK ради
аварии инфраструктуры неправильно: для оператора это тот же «бот не смог
ответить». Пишем `no_answer`, а подробность (`llm_unavailable`) остаётся
в `Reply.reason` и уйдёт в `audit_log`. Решение обратимо: если тимлид
захочет отдельную причину, это миграция на одну строку.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Conversation, Escalation

# Причины из DDL. Держим списком, чтобы опечатка ловилась здесь, а не
# исключением базы на боевом сообщении клиента.
REASON_NO_ANSWER = "no_answer"
REASON_LOW_CONFIDENCE = "low_confidence"
REASON_USER_REQUEST = "user_request"
REASON_PII_TOPIC = "pii_topic"
REASONS = frozenset(
    {REASON_NO_ANSWER, REASON_LOW_CONFIDENCE, REASON_USER_REQUEST, REASON_PII_TOPIC}
)

# Причины, которых в DDL нет, но которые случаются в коде. Схлопываем в
# ближайшую разрешённую (см. шапку модуля).
REASON_FALLBACK = {"llm_unavailable": REASON_NO_ANSWER}

# Подписчики на события инбокса. Их ставит `api/inbox.py` при старте
# приложения — так ядро не зависит от WebSocket и от FastAPI, а тесты
# ядра не поднимают сокеты.
Listener = Callable[[str, dict], Awaitable[None]]
_listeners: list[Listener] = []


def subscribe(listener: Listener) -> None:
    _listeners.append(listener)


def unsubscribe(listener: Listener) -> None:
    if listener in _listeners:
        _listeners.remove(listener)


async def notify(event: str, payload: dict) -> None:
    """Разослать событие инбоксу. Падение подписчика не ломает диалог.

    Клиент ждёт ответа, и оборванный WebSocket оператора — не повод
    уронить обработку его сообщения.
    """
    for listener in list(_listeners):
        try:
            await listener(event, payload)
        except Exception:  # noqa: BLE001 — намеренно глушим, см. docstring
            continue


def normalize_reason(reason: str | None) -> str:
    """Причина, пригодная для CHECK в базе."""
    if reason in REASONS:
        return reason
    return REASON_FALLBACK.get(reason or "", REASON_NO_ANSWER)


async def escalate(
    session: AsyncSession,
    conversation: Conversation,
    reason: str | None,
) -> Escalation:
    """Передать диалог оператору.

    Повторная эскалация уже переданного диалога новой строки НЕ создаёт:
    клиент, пишущий три сообщения подряд, не должен превращаться в три
    карточки в инбоксе и три звонка звука.
    """
    open_one = await open_escalation(session, conversation.id)
    if open_one is not None:
        return open_one

    escalation = Escalation(
        workspace_id=conversation.workspace_id,
        conversation_id=conversation.id,
        reason=normalize_reason(reason),
    )
    session.add(escalation)
    conversation.status = "operator"
    await session.flush()

    # След в аудите. Воркспейс достаём по диалогу: сюда его никто не
    # передаёт, а лишний параметр во всех вызовах ради одной записи —
    # плохой размен.
    from app.core import audit  # локально: иначе кольцевой импорт
    from app.models import Workspace

    workspace = await session.get(Workspace, conversation.workspace_id)
    if workspace is not None:
        await audit.record(
            session,
            workspace,
            audit.EVENT_ESCALATION,
            {
                "conversation_id": conversation.id,
                "escalation_id": escalation.id,
                "reason": escalation.reason,
            },
        )

    await notify(
        "new_escalation",
        {
            "conversation_id": conversation.id,
            "escalation_id": escalation.id,
            "reason": escalation.reason,
        },
    )
    return escalation


async def open_escalation(
    session: AsyncSession, conversation_id: int
) -> Escalation | None:
    """Незакрытая эскалация диалога, если она есть."""
    return await session.scalar(
        select(Escalation)
        .where(
            Escalation.conversation_id == conversation_id,
            Escalation.resolved_at.is_(None),
        )
        .order_by(Escalation.id.desc())
    )


async def take(
    session: AsyncSession, conversation: Conversation, operator: str
) -> Escalation | None:
    """Оператор взял диалог в работу.

    Повторный «взять» чужого диалога не перехватывает: `taken_by`
    проставляется один раз, иначе двое операторов будут писать клиенту
    одновременно, не видя друг друга.
    """
    escalation = await open_escalation(session, conversation.id)
    if escalation is None:
        return None
    if escalation.taken_by is None:
        escalation.taken_by = operator
        escalation.taken_at = datetime.now(timezone.utc)
        await session.flush()
        await notify(
            "taken",
            {"conversation_id": conversation.id, "taken_by": operator},
        )
    return escalation


async def close(
    session: AsyncSession, conversation: Conversation
) -> Escalation | None:
    """«Закрыть диалог»: разговор окончен, оба молчат.

    Отличие от `resolve` ровно одно, но важное: там оператор возвращает
    клиента боту и разговор продолжается, здесь — заканчивается. Дальше
    клиент пишет заново, и это будет НОВЫЙ диалог: `resolve_conversation`
    ищет незакрытый.

    Закрытие — единственный момент, когда уместно просить оценку: пока
    разговор идёт, спрашивать «как вам оператор» рано.
    """
    escalation = await open_escalation(session, conversation.id)
    if escalation is not None:
        escalation.resolved_at = datetime.now(timezone.utc)

    conversation.status = "closed"
    conversation.closed_at = datetime.now(timezone.utc)
    await session.flush()
    await notify("closed", {"conversation_id": conversation.id})
    return escalation


async def resolve(
    session: AsyncSession, conversation: Conversation
) -> Escalation | None:
    """«Вернуть боту»: закрыть эскалацию и снова пустить бота отвечать."""
    escalation = await open_escalation(session, conversation.id)
    if escalation is not None:
        escalation.resolved_at = datetime.now(timezone.utc)

    conversation.status = "bot"
    await session.flush()
    await notify("resolved", {"conversation_id": conversation.id})
    return escalation

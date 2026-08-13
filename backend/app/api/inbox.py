"""Инбокс оператора (раздел 8.3 ТЗ) — экран 06 консоли.

ОТВЕТСТВЕННОСТЬ: список эскалированных диалогов, переписка целиком,
ответ оператора в канал клиента и живые уведомления.

ЭНДПОИНТЫ (пути — из раздела 8.3 и приложения А):
  GET  /api/inbox?status=waiting|active|resolved   очередь диалогов
  GET  /api/conversations/{id}                     история + подсказка
  POST /api/conversations/{id}/take                взять в работу
  POST /api/conversations/{id}/reply               ответить клиенту
  POST /api/conversations/{id}/resolve             вернуть боту
  WS   /ws/inbox                                   события в реальном времени

ГЛАВНОЕ В ЭКРАНЕ: оператор видит ОДИН диалог с сообщениями из разных
каналов — это и есть омниканальность в данных, ради неё заведён
`conversation`, общий для всех `channel_identities` контакта.

ЧТО ЗНАЧАТ СТАТУСЫ (в ТЗ названы, но не расшифрованы):
  waiting  — эскалация есть, оператор ещё не взял (`taken_by IS NULL`);
  active   — взял, но не закрыл;
  resolved — закрыта (`resolved_at`).
Считаем по `escalations`, а не по `conversations.status`: статус диалога
не помнит, кто именно его взял и когда.

ПОДСКАЗКА ОПЕРАТОРУ — `chunks_used` ПОСЛЕДНЕГО сообщения бота, то есть
ровно те фрагменты, на которых бот сдался. ТЗ отдельно оговаривает, что
кнопка «Вставить подсказку» просто копирует текст в поле ввода, без ИИ.

ОТВЕТ УХОДИТ В КАНАЛ КЛИЕНТА — «через тот же модуль канала, которым
пришло последнее сообщение клиента». Канал берём из последнего входящего
сообщения, а не из диалога: диалог общий для всех каналов, и клиент,
начавший в Telegram и продолживший в виджете, должен получить ответ туда,
где он сейчас.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import bus, escalation
from app.core.dialog import get_workspace
from app.db import get_session
from app.models import (
    ChannelIdentity,
    Chunk,
    Contact,
    Conversation,
    Document,
    Escalation,
    Message,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["inbox"])

# Сколько сообщений отдаём в карточке. ТЗ говорит «вся история», но на
# практике это последние N: год переписки вешает экран, а оператору нужен
# хвост. Понадобится глубже — добирается пагинацией.
HISTORY_LIMIT = 200

# Прощание при закрытии диалога. Двуязычное — как все реплики бота: язык
# клиента здесь уже неизвестен, а спрашивать оценку на чужом языке хуже,
# чем на двух сразу.
GOODBYE = (
    "Мо суҳбатро мебандем. Кори мутахассисро баҳо диҳед, лутфан.\n"
    "Мы закрываем диалог. Оцените, пожалуйста, работу специалиста."
)


class ReplyIn(BaseModel):
    text: str


class TakeIn(BaseModel):
    # Логин оператора. В консоли пока один общий вход (раздел 9), но поле
    # уже есть: без него в `taken_by` нечего писать, а колонка в DDL есть.
    operator: str = "operator"


# ---------------------------------------------------------------------------
# WebSocket: события инбокса
# ---------------------------------------------------------------------------


class Hub:
    """Открытые сокеты операторов ЭТОГО процесса.

    Хаб остался в памяти — иначе и быть не может, сокет держит конкретный
    процесс. Межпроцессную доставку взяла на себя шина (`core/bus`): в
    хаб попадает то, что пришло из неё.
    """

    def __init__(self) -> None:
        self._sockets: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add(self, socket: WebSocket) -> None:
        async with self._lock:
            self._sockets.add(socket)

    async def drop(self, socket: WebSocket) -> None:
        async with self._lock:
            self._sockets.discard(socket)

    async def broadcast(self, event: str, payload: dict) -> None:
        dead = []
        for socket in list(self._sockets):
            try:
                await socket.send_json({"event": event, **payload})
            except Exception:  # noqa: BLE001 — сокет мог отвалиться молча
                dead.append(socket)
        for socket in dead:
            await self.drop(socket)


hub = Hub()


async def _relay(event: str, payload: dict) -> None:
    """Событие ядра — во все процессы, а не только в свой.

    Сокеты операторов держит конкретный процесс: с двумя воркерами
    эскалация, случившаяся в одном, не доходила до оператора, чей сокет
    висит на другом. Поэтому событие уходит в шину, а раздаёт его каждому
    своему сокету `_deliver_local` — включая тот процесс, где оно и
    родилось.
    """
    await bus.publish(bus.INBOX, {"event": event, "payload": payload})


def _deliver_local(message: dict) -> None:
    """Разослать событие сокетам ЭТОГО процесса. Зовётся шиной."""
    event, payload = message["event"], message["payload"]
    task = asyncio.create_task(hub.broadcast(event, payload))
    _relay_tasks.add(task)
    task.add_done_callback(_relay_tasks.discard)


# Задачи рассылки: без ссылки сборщик мусора вправе выбросить их на
# середине, и оператор не увидит эскалацию.
_relay_tasks: set[asyncio.Task] = set()

bus.on(bus.INBOX, _deliver_local)

# Ядро ничего не знает ни про WebSocket, ни про шину: оно зовёт
# `escalation.notify`. Так тесты ядра не поднимают ни сокетов, ни Redis.
escalation.subscribe(_relay)


@router.websocket("/ws/inbox")
async def inbox_socket(socket: WebSocket) -> None:
    await socket.accept()
    await hub.add(socket)
    try:
        while True:
            # От клиента ничего не ждём: сокет односторонний. Читаем
            # только чтобы поймать разрыв — иначе соединение висит мёртвым.
            await socket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.drop(socket)


# ---------------------------------------------------------------------------
# список и карточка
# ---------------------------------------------------------------------------


@router.get("/api/inbox")
async def list_inbox(
    status: str = Query("waiting", pattern="^(waiting|active|resolved)$"),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Левая колонка экрана 06: кто ждёт, из какого канала, с чем.

    Фильтр по воркспейсу обязателен: без него оператор одного банка видел
    в своей очереди чужие диалоги (`escalations.workspace_id` в схеме
    есть с первого дня, а в запросе его не было).
    """
    workspace = await get_workspace(session)
    query = (
        select(Escalation)
        .where(Escalation.workspace_id == workspace.id)
        .order_by(Escalation.created_at.desc())
    )
    if status == "waiting":
        query = query.where(
            Escalation.resolved_at.is_(None), Escalation.taken_by.is_(None)
        )
    elif status == "active":
        query = query.where(
            Escalation.resolved_at.is_(None), Escalation.taken_by.is_not(None)
        )
    else:
        query = query.where(Escalation.resolved_at.is_not(None))

    return [await _card(session, item) for item in (await session.scalars(query)).all()]


async def _card(session: AsyncSession, item: Escalation) -> dict:
    conversation = await session.get(Conversation, item.conversation_id)
    contact = await session.get(Contact, conversation.contact_id)
    # Берём последнее сообщение КЛИЕНТА, а не последнее вообще. Последним
    # почти всегда идёт ответ бота «соединяю со специалистом» — и очередь
    # превращалась бы в столбец одинаковых строк, по которому не выбрать,
    # кого брать первым. В эталоне (экран 06) в `.prev` тоже вопрос
    # клиента.
    last = await session.scalar(
        select(Message)
        .where(Message.conversation_id == conversation.id, Message.role == "user")
        .order_by(Message.id.desc())
    )
    return {
        "conversation_id": conversation.id,
        "escalation_id": item.id,
        "reason": item.reason,
        "created_at": item.created_at.isoformat(),
        "taken_by": item.taken_by,
        "resolved_at": item.resolved_at.isoformat() if item.resolved_at else None,
        "display_name": contact.display_name if contact else None,
        "channel": last.channel if last else None,
        # В очереди показываем МАСКУ: диалог ещё не открыт, и светить
        # номер карты в общем списке незачем.
        "preview": (last.text_masked[:80] if last else ""),
        "last_at": last.created_at.isoformat() if last else None,
    }


@router.get("/api/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    """Вся переписка контакта плюс подсказка оператору."""
    conversation = await _conversation_or_404(session, conversation_id)
    contact = await session.get(Contact, conversation.contact_id)

    messages = list(
        reversed(
            (
                await session.scalars(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.id.desc())
                    .limit(HISTORY_LIMIT)
                )
            ).all()
        )
    )

    channels = (
        await session.scalars(
            select(ChannelIdentity.channel).where(
                ChannelIdentity.contact_id == conversation.contact_id
            )
        )
    ).all()

    item = await escalation.open_escalation(session, conversation_id)
    return {
        "conversation_id": conversation.id,
        "status": conversation.status,
        "display_name": contact.display_name if contact else None,
        "channels": sorted(set(channels)),
        "escalation": (
            {
                "id": item.id,
                "reason": item.reason,
                "taken_by": item.taken_by,
                "created_at": item.created_at.isoformat(),
            }
            if item
            else None
        ),
        "messages": [
            {
                "id": message.id,
                "role": message.role,
                "channel": message.channel,
                # Оператору показываем ОРИГИНАЛ: он для того и человек,
                # чтобы видеть настоящий номер. В модель уходит только
                # text_masked — см. core/dialog.py.
                "text": message.text,
                "created_at": message.created_at.isoformat(),
                "latency_ms": message.latency_ms,
                "chunks_used": list(message.chunks_used or []),
            }
            for message in messages
        ],
        "hint": await _hint(session, messages),
    }


async def _hint(session: AsyncSession, messages: list[Message]) -> list[dict]:
    """Фрагменты последнего поиска бота — блок «Подсказка оператору».

    Берём `chunks_used` последнего сообщения бота, у которого они есть:
    ровно то, на чём бот строил ответ перед тем, как сдаться.
    """
    ids: list[int] = []
    for message in reversed(messages):
        if message.role == "assistant" and message.chunks_used:
            ids = list(message.chunks_used)
            break
    if not ids:
        return []

    chunks = (await session.scalars(select(Chunk).where(Chunk.id.in_(ids)))).all()
    by_id = {chunk.id: chunk for chunk in chunks}

    hint = []
    for chunk_id in ids:  # порядок ссылок [1], [2] сохраняем
        chunk = by_id.get(chunk_id)
        if chunk is None:
            continue
        document = await session.get(Document, chunk.document_id)
        hint.append(
            {
                "chunk_id": chunk.id,
                "title": document.title if document else "",
                "page": chunk.page,
                "text": chunk.text,
            }
        )
    return hint


# ---------------------------------------------------------------------------
# действия оператора
# ---------------------------------------------------------------------------


async def _conversation_or_404(
    session: AsyncSession, conversation_id: int
) -> Conversation:
    """Диалог ЭТОГО воркспейса. Чужой — те же 404, что несуществующий.

    Проверка воркспейса тут не формальность. Пока банк был один, её
    отсутствия никто не замечал; с появлением кнопки «Добавить банк»
    оператор одного банка, подставив чужой id, читал переписку другого и
    мог в неё ответить — ручной прогон это и показал. 404, а не 403:
    существование чужого диалога тоже не наше дело.
    """
    workspace = await get_workspace(session)
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="диалог не найден")
    return conversation


@router.post("/api/conversations/{conversation_id}/take")
async def take_conversation(
    conversation_id: int,
    payload: TakeIn | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    conversation = await _conversation_or_404(session, conversation_id)
    item = await escalation.take(
        session, conversation, (payload or TakeIn()).operator
    )
    if item is None:
        raise HTTPException(status_code=409, detail="диалог не ждёт оператора")
    await session.commit()
    return {"taken_by": item.taken_by, "conversation_id": conversation.id}


@router.post("/api/conversations/{conversation_id}/reply")
async def reply_to_client(
    conversation_id: int,
    payload: ReplyIn,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Ответ оператора уходит клиенту в его канал."""
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="пустой ответ")

    conversation = await _conversation_or_404(session, conversation_id)
    last_incoming = await session.scalar(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.role == "user")
        .order_by(Message.id.desc())
    )
    if last_incoming is None:
        raise HTTPException(status_code=409, detail="клиент ещё ничего не написал")

    from app.core.dialog import save_message

    message = await save_message(
        session,
        conversation=conversation,
        channel=last_incoming.channel,
        role="operator",
        text=text,
    )
    await _send_to_channel(session, conversation, last_incoming.channel, text)
    await session.commit()

    await escalation.notify(
        "new_message",
        {
            "conversation_id": conversation.id,
            "message_id": message.id,
            "role": "operator",
        },
    )
    return {"message_id": message.id, "channel": last_incoming.channel}


async def _send_to_channel(
    session: AsyncSession,
    conversation: Conversation,
    channel: str,
    text: str,
    rate_for: int | None = None,
) -> None:
    """Доставка в канал клиента.

    `rate_for` — id сообщения, к которому клиент может поставить оценку.
    Он есть только у прощального сообщения при закрытии диалога; в
    обычном ответе оператора его нет, и кнопки оценки не появляются.

    Каналы подключаются по мере готовности: сейчас живой только Telegram
    (раздел 7.1), виджет и WhatsApp — недели 4 и 5. Для неготового канала
    сообщение всё равно сохраняется в базе и видно в консоли, а в лог
    уходит предупреждение: молча терять ответ оператора нельзя.
    """
    if channel == "widget":
        # Виджет слушает свой SSE-поток: ответ оператора кладём туда же,
        # куда уходят ответы бота. Идентичность нужна, чтобы знать uid —
        # поток живёт на нём.
        identity = await session.scalar(
            select(ChannelIdentity).where(
                ChannelIdentity.contact_id == conversation.contact_id,
                ChannelIdentity.channel == "widget",
            )
        )
        if identity is None:
            log.warning("у контакта %s нет widget-идентичности", conversation.contact_id)
            return

        from app.channels.widget import publish

        publish(
            identity.external_id,
            "operator_msg",
            {"text": text, "rate_for": rate_for},
        )
        if rate_for is not None:
            publish(identity.external_id, "closed", {"rate_for": rate_for})
        return

    if channel != "telegram":
        log.warning("канал %s ещё не умеет отправлять — ответ только в базе", channel)
        return

    identity = await session.scalar(
        select(ChannelIdentity).where(
            ChannelIdentity.contact_id == conversation.contact_id,
            ChannelIdentity.channel == "telegram",
        )
    )
    if identity is None:
        log.warning("у контакта %s нет telegram-идентичности", conversation.contact_id)
        return

    from app.channels.telegram import get_bot, rating_keyboard

    try:
        await get_bot().send_message(
            chat_id=int(identity.external_id),
            text=text,
            # Две кнопки под прощальным сообщением. В Telegram это
            # единственный способ спросить оценку, не заставляя человека
            # печатать: ответ придёт callback-ом, а не текстом, и не
            # уедет в поиск по базе знаний.
            reply_markup=rating_keyboard(rate_for) if rate_for else None,
        )
    except Exception as exc:  # noqa: BLE001
        # Telegram может не ответить, но ответ оператора уже в базе и на
        # экране — падать поздно, важно оставить след.
        log.warning("не удалось отправить в Telegram: %s", exc)


@router.post("/api/conversations/{conversation_id}/resolve")
async def resolve_conversation(
    conversation_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    """«Вернуть боту»."""
    conversation = await _conversation_or_404(session, conversation_id)
    await escalation.resolve(session, conversation)
    await session.commit()
    return {"conversation_id": conversation.id, "status": conversation.status}


@router.post("/api/conversations/{conversation_id}/close")
async def close_conversation(
    conversation_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    """«Закрыть диалог» и попросить клиента оценить работу оператора.

    ПОЧЕМУ ОЦЕНКА ЖИВЁТ ЗДЕСЬ, А НЕ В КАНАЛЕ. Спрашивать «как вам
    оператор» имеет смысл ровно один раз и ровно в этот момент. Канал не
    знает, когда разговор окончен, — это решает человек, нажимая кнопку.

    Сообщение с просьбой сохраняем как реплику оператора, а не как
    `system`: клиент видит его в переписке, а `system` каналы намеренно
    не показывают.
    """
    conversation = await _conversation_or_404(session, conversation_id)
    if conversation.status == "closed":
        raise HTTPException(status_code=409, detail="диалог уже закрыт")

    last_incoming = await session.scalar(
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.role == "user")
        .order_by(Message.id.desc())
    )

    from app.core.dialog import save_message

    message = None
    if last_incoming is not None:
        message = await save_message(
            session,
            conversation=conversation,
            channel=last_incoming.channel,
            role="operator",
            text=GOODBYE,
        )

    await escalation.close(session, conversation)
    await session.commit()

    if last_incoming is not None:
        # `rate_for` — сообщение, к которому привяжется оценка. Без него
        # клиенту некуда её поставить: в схеме feedback ссылается на
        # `messages.id`, а не на диалог.
        await _send_to_channel(
            session,
            conversation,
            last_incoming.channel,
            GOODBYE,
            rate_for=message.id if message else None,
        )

    return {
        "conversation_id": conversation.id,
        "status": conversation.status,
        "rate_for": message.id if message else None,
    }


@router.get("/api/inbox/counters")
async def counters(session: AsyncSession = Depends(get_session)) -> dict:
    """Бейдж непрочитанного в меню консоли — по своему банку."""
    workspace = await get_workspace(session)
    waiting = await session.scalar(
        select(func.count())
        .select_from(Escalation)
        .where(
            Escalation.workspace_id == workspace.id,
            Escalation.resolved_at.is_(None),
            Escalation.taken_by.is_(None),
        )
    )
    active = await session.scalar(
        select(func.count())
        .select_from(Escalation)
        .where(
            Escalation.workspace_id == workspace.id,
            Escalation.resolved_at.is_(None),
            Escalation.taken_by.is_not(None),
        )
    )
    return {"waiting": waiting or 0, "active": active or 0}

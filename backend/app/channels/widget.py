"""Веб-виджет: серверная часть (раздел 7.2 ТЗ).

ОТВЕТСТВЕННОСТЬ: эндпоинты для iframe виджета — приём сообщения, отдача
ответа стримом (SSE), выдача `link_token` для перехода в Telegram.

  POST /widget/messages    → 202 {"message_id": ...}
  GET  /widget/stream      → SSE: history, delta, final, escalated,
                             operator_msg, closed
  POST /widget/link-token  → {"token": ..., "url": "https://t.me/..."}
  POST /widget/feedback    → оценка работы оператора после закрытия

ИДЕНТИФИКАЦИЯ: `uid` — `crypto.randomUUID()`, который фронт кладёт в
localStorage под ключом `soro_uid` и присылает с каждым запросом; он же
`external_id` для канала `widget`. Регистрации нет — раздел 1.2 выносит
её за скобки версии.

  РАСХОЖДЕНИЕ С ПРЕЖНЕЙ ЗАПИСЬЮ В ЭТОМ ФАЙЛЕ: здесь стояло «cookie-uuid».
  Раздел 7.2 говорит про localStorage и явный параметр, и это не
  придирка: виджет живёт в iframe на чужом домене, то есть в
  third-party-контексте, где Safari и Firefox режут куки по умолчанию.
  Кука молча не доехала бы, и «виджет помнит диалог» перестало бы
  работать ровно у той половины зала, что сидит с айфонами.

СТРИМИНГ. Поток живёт на `uid`, а не на сообщение: он открывается при
загрузке виджета, сразу отдаёт `history` (это и есть «виджет помнит
диалог» из сценария экрана 04) и остаётся открытым — в него же приходят
ответы бота и реплики оператора. Поэтому POST возвращает 202 и не ждёт
ответа: работу делает фоновая задача, а её результат уезжает в поток.

СКЛЕЙКА С TELEGRAM: кнопка «Продолжить в Telegram» открывает
`t.me/<бот>?start=<link_token>`; токен — 24 случайных символа, живёт в
Redis 15 минут.

ЗАВИСИМОСТИ: core.dialog, core.linking, Redis, models.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from redis import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import dialog, feedback
from app.db import SessionLocal, get_session
from app.models import Chunk, ChannelIdentity, Conversation, Document, Message

log = logging.getLogger(__name__)

router = APIRouter(prefix="/widget", tags=["widget"])

CHANNEL = "widget"

# 15 минут — столько живёт токен. Меньше не стоит: человек нажимает
# «Продолжить в Telegram», а дальше ищет приложение, логинится, отвлекается
# на звонок. Больше — тоже: ссылку с токеном пересылают, и чем дольше он
# живёт, тем выше шанс, что перепиской завладеет не тот человек.
TOKEN_TTL = 15 * 60

# 18 случайных байт в base64url — ровно 24 символа, как в разделе 5.1.
TOKEN_BYTES = 18

KEY_PREFIX = "link:"


def _redis() -> Redis:
    """Клиент создаётся на вызов, а не на модуль.

    Так же сделано в `api/console.py` с очередью: модульный клиент
    подключался бы при импорте, и тесты HTTP-слоя потребовали бы живой
    Redis ради проверки, которая до него не доходит.
    """
    return Redis.from_url(settings.REDIS_URL)


def issue_token(identity_id: int) -> str:
    """Выдать одноразовый токен для идентичности виджета."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    _redis().setex(KEY_PREFIX + token, TOKEN_TTL, str(identity_id))
    return token


def take_token(token: str) -> int | None:
    """Обменять токен на идентичность виджета. Токен сгорает.

    Одноразовость важнее удобства: ссылка `t.me/bot?start=<токен>` уходит в
    историю браузера и в буфер обмена, и второй переход по ней должен
    приводить к обычному новому диалогу, а не к чужой переписке.
    """
    if not token:
        return None

    # GETDEL — одна операция вместо GET + DEL: между ними два перехода по
    # одной ссылке успели бы склеиться оба.
    raw = _redis().getdel(KEY_PREFIX + token)
    if raw is None:
        log.info("токен склейки не найден или истёк")
        return None
    return int(raw)


def telegram_link(token: str) -> str:
    """Ссылка для кнопки «Продолжить в Telegram»."""
    return f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={token}"


# --- шина потоков ----------------------------------------------------------
#
# Событие рождается там, где нет запроса клиента: ответ бота считает
# фоновая задача, реплику оператора приносит инбокс. Дотянуться до
# открытого SSE-соединения им нужно через что-то общее — вот через это.
#
# ПОЧЕМУ В ПАМЯТИ, А НЕ ЧЕРЕЗ REDIS PUB/SUB. Бэкенд — один процесс, как и
# у операторского WebSocket в `api/inbox.py`. Появится второй воркер —
# понадобится Redis, и менять придётся ровно эти три функции.

# uid → очереди открытых потоков. Вкладок у одного клиента может быть
# несколько, и каждая ждёт свою копию событий.
_streams: dict[str, set[asyncio.Queue]] = {}

# Потолок на очередь: клиент с закрытым ноутбуком не должен копить ответы
# бесконечно. Дальше события выбрасываются — поток он всё равно не читает.
QUEUE_LIMIT = 100

# Как часто напоминать о себе молчащему потоку.
KEEPALIVE_SEC = 20

# Живые фоновые задачи ответа: см. `post_message`.
_tasks: set[asyncio.Task] = set()


def publish(uid: str, event: str, data: dict) -> None:
    """Разослать событие всем открытым потокам этого клиента."""
    for queue in _streams.get(uid, set()):
        if queue.qsize() >= QUEUE_LIMIT:
            log.warning("поток %s не читают, событие %s выброшено", uid, event)
            continue
        queue.put_nowait((event, data))


def _subscribe(uid: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue()
    _streams.setdefault(uid, set()).add(queue)
    return queue


def _unsubscribe(uid: str, queue: asyncio.Queue) -> None:
    listeners = _streams.get(uid)
    if not listeners:
        return
    listeners.discard(queue)
    if not listeners:
        _streams.pop(uid, None)


# --- эндпоинты -------------------------------------------------------------


class MessageIn(BaseModel):
    uid: str
    text: str
    # Воркспейс приходит из data-ws в теге на сайте банка. В этой версии
    # он один, но подпись эндпоинта не должна этого предполагать.
    ws: str | None = None


class LinkIn(BaseModel):
    uid: str
    ws: str | None = None


def sse(event: str, data: dict) -> str:
    """Кадр SSE. `ensure_ascii=False` обязателен: иначе таджикский текст
    уедет в \\uXXXX и раздует поток втрое."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/messages", status_code=202)
async def post_message(payload: MessageIn) -> dict:
    """Принять вопрос и сразу отпустить браузер.

    Отвечать здесь нечем: ответ идёт в поток, открытый до этого запроса.
    Держать соединение до конца генерации значит ронять вопрос по
    таймауту прокси на первом же длинном ответе.
    """
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="пустой вопрос")
    if not payload.uid.strip():
        raise HTTPException(status_code=422, detail="нет uid")

    message_id = uuid.uuid4().hex
    # Ссылку на задачу держим, пока она жива: без неё сборщик мусора
    # вправе выбросить задачу на середине, и ответ просто не придёт.
    task = asyncio.create_task(_answer(payload.uid, payload.ws, text))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"message_id": message_id}


async def _answer(uid: str, ws: str | None, text: str) -> None:
    """Прогнать вопрос через ядро и разложить результат по событиям.

    Своя сессия, а не сессия запроса: тот уже ответил 202 и закрыт.
    """
    try:
        async with SessionLocal() as session:
            reply = await dialog.handle_incoming(
                session,
                channel=CHANNEL,
                external_id=uid,
                text=text,
                workspace_slug=ws,
                on_delta=lambda piece: publish(uid, "delta", {"text": piece}),
            )
    except Exception:  # noqa: BLE001 — фоновой задаче некому передать ошибку
        log.exception("виджет: ответ не собрался")
        publish(uid, "error", {"message": "Не удалось получить ответ"})
        return

    if reply is None:
        # Диалогом занимается оператор — бот молчит (см. `handle_incoming`).
        # Пустой `final` нужен всё равно: он гасит «печатает…» в виджете.
        publish(uid, "final", {"text": "", "chunks_used": [], "sources": []})
        return

    async with SessionLocal() as session:
        sources = await _sources(session, reply.chunks_used)

    # `final` заменяет собой всё, что уехало кусками: ядро могло подменить
    # текст фразой об эскалации уже после того, как поток закончился.
    publish(
        uid,
        "final",
        {
            "message_id": reply.message_id,
            "text": reply.text,
            "chunks_used": reply.chunks_used,
            "sources": sources,
        },
    )
    if reply.escalated:
        publish(uid, "escalated", {"reason": reply.reason})


@router.get("/stream")
async def stream(uid: str, ws: str | None = None) -> StreamingResponse:
    """Долгоживущий поток событий одного клиента.

    Открывается при загрузке виджета и живёт, пока открыта вкладка: в него
    приходит и история, и ответы бота, и реплики оператора.
    """
    if not uid.strip():
        raise HTTPException(status_code=422, detail="нет uid")

    queue = _subscribe(uid)

    async def events():
        try:
            async with SessionLocal() as session:
                history = await _history(session, uid, ws)
            yield sse("history", {"messages": history})

            while True:
                try:
                    event, data = await asyncio.wait_for(
                        queue.get(), timeout=KEEPALIVE_SEC
                    )
                except asyncio.TimeoutError:
                    # Комментарий SSE: браузеру он невидим, а прокси и
                    # мобильный оператор видят трафик и не рвут соединение.
                    yield ": keepalive\n\n"
                    continue
                yield sse(event, data)
        finally:
            _unsubscribe(uid, queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Nginx буферизует ответы по умолчанию, и поток приходит одним
            # куском в конце — то есть перестаёт быть потоком.
            "X-Accel-Buffering": "no",
        },
    )


class FeedbackIn(BaseModel):
    uid: str
    message_id: int
    score: int
    ws: str | None = None


@router.post("/feedback")
async def rate(
    payload: FeedbackIn, session: AsyncSession = Depends(get_session)
) -> dict:
    """Оценка работы оператора после закрытия диалога.

    Эндпоинт открыт наружу, поэтому оценить можно только СВОЁ сообщение:
    иначе любой желающий испортит статистику чужого банка, зная лишь
    порядковый номер строки в `messages`.
    """
    workspace = await dialog.get_workspace(session, payload.ws)
    identity = await session.scalar(
        select(ChannelIdentity).where(
            ChannelIdentity.workspace_id == workspace.id,
            ChannelIdentity.channel == CHANNEL,
            ChannelIdentity.external_id == payload.uid,
        )
    )
    if identity is None:
        raise HTTPException(status_code=404, detail="клиент не найден")

    message = await session.get(Message, payload.message_id)
    conversation = (
        await session.get(Conversation, message.conversation_id) if message else None
    )
    if conversation is None or conversation.contact_id != identity.contact_id:
        raise HTTPException(status_code=403, detail="это не ваш диалог")

    saved = await feedback.record(session, payload.message_id, payload.score)
    if saved is None:
        raise HTTPException(status_code=422, detail="оценка вне допустимых значений")
    await session.commit()
    return {"ok": True, "score": saved.score}


@router.post("/link-token")
async def link_token(
    payload: LinkIn, session: AsyncSession = Depends(get_session)
) -> dict:
    """Токен для кнопки «Продолжить в Telegram».

    Идентичность заводим, если её ещё нет: клиент мог нажать кнопку, не
    написав ни слова, — например, чтобы дочитать с телефона в метро.
    """
    workspace = await dialog.get_workspace(session, payload.ws)
    identity = await dialog.resolve_identity(
        session, workspace.id, CHANNEL, payload.uid
    )
    await session.commit()

    token = issue_token(identity.id)
    return {"token": token, "url": telegram_link(token)}


# --- вспомогательное -------------------------------------------------------


async def _history(session: AsyncSession, uid: str, ws: str | None) -> list[dict]:
    """Переписка этого клиента — то, чем виджет «помнит диалог».

    История берётся по КОНТАКТУ, а не по каналу: в этом весь смысл склейки
    из раздела 5.1. Клиент, пришедший из Telegram по `link_token`, увидит
    в виджете свой разговор с ботом — сценарий экрана 04 ровно про это.
    """
    workspace = await dialog.get_workspace(session, ws)
    identity = await session.scalar(
        select(ChannelIdentity).where(
            ChannelIdentity.workspace_id == workspace.id,
            ChannelIdentity.channel == CHANNEL,
            ChannelIdentity.external_id == uid,
        )
    )
    if identity is None:
        return []

    conversation = await session.scalar(
        select(Conversation)
        .where(
            Conversation.workspace_id == workspace.id,
            Conversation.contact_id == identity.contact_id,
            Conversation.status != "closed",
        )
        .order_by(Conversation.last_msg_at.desc())
    )
    if conversation is None:
        return []

    messages = (
        await session.scalars(
            select(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.role != "system",
            )
            .order_by(Message.id)
        )
    ).all()

    return [
        {
            "role": message.role,
            # Оригинал, а не маска: это переписка клиента с самим собой,
            # и свой номер карты он вправе видеть.
            "text": message.text,
            "created_at": message.created_at.isoformat(),
            "chunks_used": list(message.chunks_used or []),
        }
        for message in messages
    ]


async def _sources(session: AsyncSession, chunk_ids: list[int]) -> list[dict]:
    """Бейджи [1] [2] под ответом: документ и страница.

    Порядок ссылок сохраняем — номер бейджа обязан совпасть с номером в
    тексте ответа.
    """
    if not chunk_ids:
        return []

    chunks = (
        await session.scalars(select(Chunk).where(Chunk.id.in_(chunk_ids)))
    ).all()
    by_id = {chunk.id: chunk for chunk in chunks}

    sources = []
    for number, chunk_id in enumerate(chunk_ids, start=1):
        chunk = by_id.get(chunk_id)
        if chunk is None:
            continue
        document = await session.get(Document, chunk.document_id)
        sources.append(
            {
                "n": number,
                "chunk_id": chunk.id,
                "title": document.title if document else "",
                "page": chunk.page,
                "source_url": document.source_url if document else None,
            }
        )
    return sources

"""Площадка — экран 03 «стеклянный ящик» (приложение А ТЗ).

ОТВЕТСТВЕННОСТЬ: показать в реальном времени, что происходит внутри при
ответе на вопрос: какие фрагменты нашлись, с какой близостью, сколько
заняли поиск и генерация.

Два эндпоинта, как в приложении А:
  POST /api/playground/messages  → 202 {"message_id": ...}
  GET  /api/playground/stream    → SSE с событиями

СОБЫТИЯ (порядок обязателен, требование приложения А):
  retrieval — фрагменты со score, приходит ДО начала генерации;
  delta     — очередной кусок ответа;
  final     — готовый текст, chunks_used и телеметрия;
  error     — что-то сломалось, фронт показывает это вместо ответа.

ПОЧЕМУ ПЛОЩАДКА НЕ ХОДИТ ЧЕРЕЗ `core/dialog.py`. Диалог заводит контакт,
идентичность и запись в `messages` — то есть каждый тестовый вопрос
сотрудника банка попадал бы в инбокс оператора и в аналитику. Площадка
для того и сделана, чтобы проверять на ней, а не чтобы засорять
статистику демо. Логика ответа при этом та же: порог 6.4, затем модель.

ПАМЯТЬ ДИАЛОГА. Фронт присылает `thread_id` — один на вкладку, — и по
нему здесь копится история реплик. В каналах эту роль играет
`conversations`, но площадка намеренно не пишет в базу, поэтому история
лежит в памяти процесса рядом с `_pending`. Без неё бот не понимал ответ
на собственный уточняющий вопрос; подробности и замеры — в шапке
`core/context.py`.

ПОЧЕМУ POST И SSE РАЗДЕЛЕНЫ. Так требует приложение А, и так же устроен
виджет. Браузерный EventSource умеет только GET и не умеет тело запроса —
значит вопрос надо сначала передать отдельным POST, а потом открыть на
него поток. Вопрос между двумя запросами лежит в памяти процесса:
консоль — один процесс и один пользователь, городить ради этого очередь
незачем. Переживёт ли перезапуск бэкенда — нет, и не должен: фронт на 404
просто отправит вопрос заново.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import audit, context, llm, policy
from app.core.dialog import (
    NO_ANSWER_REPLY,
    REPEAT_REPLY,
    get_workspace,
    search_in_context,
    smalltalk_reply,
)
from app.core.pii import mask
from app.db import get_session

router = APIRouter(prefix="/api/playground", tags=["playground"])

# Сколько живёт неотвеченный вопрос. Фронт открывает поток сразу после
# POST, так что минуты хватает с запасом; всё, что старше, — брошенная
# вкладка, и держать её в памяти незачем.
PENDING_TTL_SEC = 60

# Оценка токенов, если сервер модели не прислал usage. Тот же коэффициент,
# что у чанкера (`ingest/chunker.py`), — чтобы цифры на экране 03 и в
# замерах индексации считались одинаково.
CHARS_PER_TOKEN = 3.5


# Сколько живёт история площадки без новых вопросов. Полчаса — это
# «сотрудник отошёл на совещание и вернулся к тому же разговору»; сутки
# держать незачем, а минуты мало.
THREAD_TTL_SEC = 30 * 60

# Потолок на число одновременно живущих веток. Площадка — внутренний
# экран на одного-двух человек, но вкладку открывают и забывают, и без
# потолка словарь растёт до перезапуска.
MAX_THREADS = 50


@dataclass
class Pending:
    question: str
    thread_id: str | None = None
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class Thread:
    """История одной ветки «Площадки» — то же, что диалог в каналах.

    В каналах история лежит в `messages` и переживает перезапуск. Здесь
    её негде хранить: площадка намеренно не пишет в базу, иначе каждый
    тестовый вопрос сотрудника банка попадал бы в инбокс и в аналитику
    (см. шапку модуля). Поэтому история живёт в памяти процесса рядом с
    `_pending` и по тем же правилам: перезапуск её теряет, и это
    нормально — фронт начинает новую ветку.
    """

    turns: list[context.Turn] = field(default_factory=list)
    touched_at: float = field(default_factory=time.monotonic)


# вопрос между POST и SSE; см. комментарий в шапке модуля
_pending: dict[str, Pending] = {}
# история по веткам: ключ приходит с фронта, один на вкладку
_threads: dict[str, Thread] = {}


class MessageIn(BaseModel):
    text: str
    # Ветка диалога. Пустое значение = разговор без памяти: так ведут
    # себя старые клиенты и curl, и ломаться они не должны.
    thread_id: str | None = None


def _sweep() -> None:
    """Выбросить протухшие вопросы и ветки. Дешевле фонового таймера."""
    now = time.monotonic()
    for key in [
        k for k, v in _pending.items() if now - v.created_at > PENDING_TTL_SEC
    ]:
        _pending.pop(key, None)

    for key in [
        k for k, v in _threads.items() if now - v.touched_at > THREAD_TTL_SEC
    ]:
        _threads.pop(key, None)


def _history(thread_id: str | None) -> list[context.Turn]:
    thread = _threads.get(thread_id or "")
    return list(thread.turns) if thread else []


def _remember(thread_id: str | None, question: str, answer: str) -> None:
    """Дописать обмен в историю ветки.

    Записываем ВОПРОС КАК ЕГО НАПИСАЛ ЧЕЛОВЕК, а не переписанный поиском:
    история — это разговор, а переписанная формулировка нужна только
    поиску и живёт ровно один запрос.
    """
    if not thread_id:
        return

    # Потолок держим здесь, а не в `_sweep`: тот работает до того, как
    # ветка заведена, и на один разговор мы всё равно вылезали за предел.
    while thread_id not in _threads and len(_threads) >= MAX_THREADS:
        oldest = min(_threads, key=lambda key: _threads[key].touched_at)
        _threads.pop(oldest, None)

    thread = _threads.setdefault(thread_id, Thread())
    thread.turns.append(context.Turn("user", question))
    thread.turns.append(context.Turn("assistant", answer))
    # Храним чуть больше, чем уйдёт в промпт: `context.trim` сам возьмёт
    # сколько нужно, а запас позволит когда-нибудь показать ветку целиком.
    thread.turns = thread.turns[-2 * context.HISTORY_TURNS :]
    thread.touched_at = time.monotonic()


@router.post("/messages", status_code=202)
async def post_message(payload: MessageIn) -> dict:
    """Принять вопрос и вернуть id, по которому фронт откроет поток."""
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="пустой вопрос")

    _sweep()
    message_id = uuid.uuid4().hex
    _pending[message_id] = Pending(question=text, thread_id=payload.thread_id)
    return {"message_id": message_id}


def sse(event: str, data: dict) -> str:
    """Кадр SSE. `ensure_ascii=False` обязателен: иначе таджикский текст
    уедет в \\uXXXX и раздует поток втрое."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/stream")
async def stream(
    message_id: str, session: AsyncSession = Depends(get_session)
) -> StreamingResponse:
    """Поток ответа на ранее отправленный вопрос."""
    pending = _pending.pop(message_id, None)
    if pending is None:
        raise HTTPException(status_code=404, detail="вопрос не найден или устарел")

    workspace = await get_workspace(session)
    # В поиск и в модель уходит маска — тот же инвариант, что в dialog.py.
    # На площадке это ещё и наглядно: сотрудник банка видит, что номер
    # карты до модели не доходит.
    question = (
        mask(pending.question)
        if policy.enabled(workspace, policy.MASK_PII)
        else pending.question
    )

    async def events():
        started = time.monotonic()
        try:
            # Вежливость без вопроса обрабатывается тем же правилом, что и
            # в каналах: иначе «салом» на площадке отвечает эскалацией, а в
            # Telegram — приветствием, и демо противоречит само себе.
            smalltalk = smalltalk_reply(pending.question, workspace)
            if smalltalk is not None:
                yield sse(
                    "retrieval",
                    {
                        "question": question,
                        "has_answer": True,
                        "best_score": 0.0,
                        "min_score": settings.RAG_MIN_SCORE,
                        "search_ms": 0,
                        # искать было нечего: это приветствие, а не вопрос
                        "fragments": [],
                    },
                )
                yield sse("delta", {"text": smalltalk})
                _remember(pending.thread_id, pending.question, smalltalk)
                yield sse(
                    "final",
                    {
                        "text": smalltalk,
                        "chunks_used": [],
                        "escalated": False,
                        "reason": None,
                        "telemetry": {
                            "search_ms": 0,
                            "generation_ms": 0,
                            "total_ms": int((time.monotonic() - started) * 1000),
                            "tokens": 0,
                        },
                    },
                )
                return

            history = _history(pending.thread_id)
            outcome = await search_in_context(
                session, workspace.id, question, history
            )
            found = outcome.result
            search_ms = int((time.monotonic() - started) * 1000)

            yield sse(
                "retrieval",
                {
                    "question": question,
                    # Чем на самом деле искали. «Стеклянный ящик» обязан
                    # это показывать: иначе фрагменты не сходятся с
                    # вопросом на экране и выглядят как ошибка поиска.
                    "searched": outcome.query,
                    "rewritten": outcome.rewritten,
                    "has_answer": found.has_answer,
                    "best_score": round(found.best_score, 3),
                    "min_score": settings.RAG_MIN_SCORE,
                    "search_ms": search_ms,
                    "fragments": [
                        {
                            "n": number,
                            "chunk_id": hit.chunk_id,
                            "title": hit.title,
                            "page": hit.page,
                            "source_url": hit.source_url,
                            "score": round(hit.score, 3),
                            "text": hit.text,
                        }
                        for number, hit in enumerate(found.hits, start=1)
                    ],
                },
            )

            if not found.has_answer:
                # Ни один фрагмент не прошёл порог — модель не зовём.
                # Экран показывает это отдельным пустым состоянием.
                #
                # В историю кладём фразу об эскалации, а не пустоту: для
                # следующего вопроса важно, что бот НЕ ответил. Иначе
                # модель, увидев пустую реплику, решит, что ответила, и
                # начнёт ссылаться на несказанное.
                _remember(pending.thread_id, pending.question, NO_ANSWER_REPLY)
                yield sse(
                    "final",
                    {
                        "text": "",
                        "chunks_used": [],
                        "escalated": True,
                        "reason": llm.REASON_NO_ANSWER,
                        "telemetry": {
                            "search_ms": search_ms,
                            "generation_ms": 0,
                            "total_ms": int((time.monotonic() - started) * 1000),
                            "tokens": 0,
                        },
                    },
                )
                return

            generation_started = time.monotonic()
            pieces: list[str] = []
            async for piece in llm.stream_answer(
                question, found.hits, bank_name=workspace.name, history=history
            ):
                pieces.append(piece)
                yield sse("delta", {"text": piece})

            raw = "".join(pieces)
            text, escalated, reason = llm.parse_answer(raw, found.hits, question)
            chunks_used = llm.cited_chunk_ids(text, found.hits)

            if context.is_repeat(text, history, question):
                # Модель пересказала свой прошлый ответ — нового во
                # фрагментах нет. Куски уже ушли в поток, и `final` их
                # заменит: на экране это заметно, но честнее, чем в
                # третий раз показать клиенту тот же абзац.
                text, escalated, reason = REPEAT_REPLY, True, llm.REASON_NO_ANSWER
                chunks_used = []

            if not policy.enabled(workspace, policy.CITE_SOURCES):
                text = llm.strip_citations(text)
                chunks_used = []
            generation_ms = int((time.monotonic() - generation_started) * 1000)

            await audit.record(
                session,
                workspace,
                audit.EVENT_LLM_CALL,
                {
                    "question": question,
                    "chunk_ids": llm.cited_chunk_ids(text, found.hits),
                    "latency_ms": generation_ms,
                    "model": settings.SORO_MODEL,
                    "escalated": escalated,
                    "source": "playground",
                },
            )
            # Коммит нужен явно: площадка больше ничего в базу не пишет, а
            # `record` делает только flush — в `dialog.py` он часть большей
            # транзакции, и коммитить там будет вызывающий.
            await session.commit()

            _remember(pending.thread_id, pending.question, text)

            yield sse(
                "final",
                {
                    "text": text,
                    "chunks_used": chunks_used,
                    "escalated": escalated,
                    "reason": reason,
                    "telemetry": {
                        "search_ms": search_ms,
                        "generation_ms": generation_ms,
                        "total_ms": int((time.monotonic() - started) * 1000),
                        "tokens": int(len(raw) / CHARS_PER_TOKEN),
                    },
                },
            )
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            # Модель недоступна. На площадке это надо показать честно, а не
            # прятать за вежливой фразой: экран для того и нужен, чтобы
            # ИТ-служба видела, что именно сломалось.
            yield sse(
                "error",
                {
                    "message": "Модель недоступна",
                    "detail": f"{type(exc).__name__}: {exc}",
                },
            )
        except asyncio.CancelledError:
            # вкладку закрыли посреди генерации — это норма, не ошибка
            raise
        finally:
            # Соединение к базе возвращаем руками. FastAPI закрывает
            # зависимость с `yield` по завершении запроса, но у
            # StreamingResponse тело живёт дольше, а при обрыве потока
            # (закрыли вкладку, ушли с экрана) уборка может не случиться
            # вовсе. Найдено прогоном: шестьдесят оборванных потоков подряд
            # выбрали весь пул, и бэкенд начал отвечать 500
            # «QueuePool limit of size 5 overflow 10 reached».
            await session.close()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # nginx и прочие прокси буферизуют ответ и съедают весь смысл
            # стриминга: ответ приходит целиком в конце
            "X-Accel-Buffering": "no",
        },
    )

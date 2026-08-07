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
from app.core import llm, rag
from app.core.dialog import get_workspace, smalltalk_reply
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


@dataclass
class Pending:
    question: str
    created_at: float = field(default_factory=time.monotonic)


# вопрос между POST и SSE; см. комментарий в шапке модуля
_pending: dict[str, Pending] = {}


class MessageIn(BaseModel):
    text: str


def _sweep() -> None:
    """Выбросить протухшие вопросы. Дешевле фонового таймера."""
    now = time.monotonic()
    for key in [
        k for k, v in _pending.items() if now - v.created_at > PENDING_TTL_SEC
    ]:
        _pending.pop(key, None)


@router.post("/messages", status_code=202)
async def post_message(payload: MessageIn) -> dict:
    """Принять вопрос и вернуть id, по которому фронт откроет поток."""
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="пустой вопрос")

    _sweep()
    message_id = uuid.uuid4().hex
    _pending[message_id] = Pending(question=text)
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
    question = mask(pending.question)

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

            found = await rag.search(session, question, workspace.id)
            search_ms = int((time.monotonic() - started) * 1000)

            yield sse(
                "retrieval",
                {
                    "question": question,
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
                question, found.hits, bank_name=workspace.name
            ):
                pieces.append(piece)
                yield sse("delta", {"text": piece})

            raw = "".join(pieces)
            text, escalated, reason = llm.parse_answer(raw, found.hits, question)
            generation_ms = int((time.monotonic() - generation_started) * 1000)

            yield sse(
                "final",
                {
                    "text": text,
                    "chunks_used": llm.cited_chunk_ids(text, found.hits),
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

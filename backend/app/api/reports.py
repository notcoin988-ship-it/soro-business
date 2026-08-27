"""Отчёт словами — экран 08 консоли.

  POST /api/reports/ask   {"question": "нужен отчёт за эту неделю"}

ОТВЕТСТВЕННОСТЬ: принять фразу, отдать её `core.reports` и вернуть наружу
всё, из чего собран ответ: период, посчитанные цифры, сводку и текст.
Логики здесь нет — она в ядре, потому что тот же путь обслуживает Telegram.

ПОЧЕМУ В ОТВЕТЕ И ТЕКСТ, И СВОДКА, И СЫРЫЕ ЦИФРЫ. Экран показывает рядом
с ответом ровно то, что видела модель. Это не отладка, а главный аргумент
на встрече: «модель не считает и не выдумывает, она пересказывает вот эти
семь строк, посчитанные запросом к базе».

БЕЗ СТРИМИНГА, в отличие от «Площадки»: живой замер — 2,9 с на весь отчёт
и 0,4 с до первой буквы (см. `llm.complete`). Ради трёх секунд экрану не
нужен разбор SSE.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import reports
from app.core.dialog import get_workspace
from app.db import get_session

router = APIRouter(prefix="/api", tags=["reports"])


class AskIn(BaseModel):
    # Потолок тот же, что у вопроса клиента: длиннее тысячи символов
    # просьбу об отчёте не формулируют, а поле без предела — это способ
    # прислать в промпт мегабайт.
    question: str = Field(min_length=1, max_length=settings.MESSAGE_MAX_CHARS)


@router.post("/reports/ask")
async def ask(payload: AskIn, session: AsyncSession = Depends(get_session)) -> dict:
    """Отчёт по фразе руководителя."""
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="пустой запрос")

    workspace = await get_workspace(session)
    report = await reports.build(
        session, question, workspace=workspace, channel="console"
    )
    # Аудит пишется внутри `build` в ту же сессию — коммит здесь, иначе
    # запись о выгрузке цифр откатится вместе с запросом.
    await session.commit()

    return {
        "question": question,
        "period": {
            "name": report.period.name,
            "title": report.period.title,
            "since": report.period.since.isoformat(),
            "until": report.period.until.isoformat(),
            "days": report.period.days,
            "assumed": report.period.assumed,
        },
        "text": report.text,
        # Та же сводка, что ушла в модель, — её показывает панель справа.
        "facts": report.facts,
        "data": report.data,
        "warnings": report.warnings,
        "degraded": report.degraded,
        "latency_ms": report.latency_ms,
    }

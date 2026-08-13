"""Оценка работы оператора клиентом (таблица `feedback` раздела 5).

ПЯТЬ ЗВЁЗД, А НЕ ПАЛЕЦ. В исходном DDL стояло `CHECK (score IN (-1, 1))`,
а прототип на экране 07 обещал «4,4/5 по 380 оценкам» — противоречие
внутри самого ТЗ. Решено в пользу прототипа: его показывают банку, и
«4,4 из 5» на защите бюджета говорит больше, чем «82% довольных».
Шкалу поменяла миграция 0003, старые пальцы переехали как 1 → 5 и
-1 → 1.

ОДНА ОЦЕНКА НА СООБЩЕНИЕ. Клиент может нажать дважды — из пересланного
сообщения, с двух вкладок, случайно. Вторая оценка перезаписывает первую,
а не добавляется: иначе один человек накрутит статистику в любую сторону.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Feedback, Message

log = logging.getLogger(__name__)

# Границы из CHECK: больше ничего в колонку не положить.
MIN_SCORE = 1
MAX_SCORE = 5
SCORES = tuple(range(MIN_SCORE, MAX_SCORE + 1))


async def record(session: AsyncSession, message_id: int, score: int) -> Feedback | None:
    """Записать оценку сообщения. `None` — оценивать нечего или балл чужой."""
    if score not in SCORES:
        log.warning("оценка %r вне шкалы %s–%s", score, MIN_SCORE, MAX_SCORE)
        return None

    message = await session.get(Message, message_id)
    if message is None:
        log.warning("оценка на несуществующее сообщение %s", message_id)
        return None

    existing = await session.scalar(
        select(Feedback).where(Feedback.message_id == message_id)
    )
    if existing is not None:
        existing.score = score
        await session.flush()
        return existing

    feedback = Feedback(
        workspace_id=message.workspace_id,
        message_id=message_id,
        score=score,
    )
    session.add(feedback)
    await session.flush()
    return feedback

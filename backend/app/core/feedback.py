"""Оценка работы оператора клиентом (таблица `feedback` раздела 5).

ПОЧЕМУ ПАЛЕЦ, А НЕ ПЯТЬ ЗВЁЗД. В DDL раздела 5 у оценки стоит
`CHECK (score IN (-1, 1))` — это большой палец вверх или вниз на
конкретное сообщение. Прототип на экране 07 при этом обещает «4,4/5 по
380 оценкам», то есть пятибалльную шкалу. Противоречие в самом ТЗ:
пятибалльную шкалу в эту колонку не положить.

Сделано по DDL, потому что схема — это то, что уже развёрнуто и на что
ссылается миграция 0001; переезд на пять баллов означает миграцию с
изменением CHECK и правку экрана 07. Решение за тимлидом, вопрос про
`feedback` и так стоит в списке «не назначено никому».

ОДНА ОЦЕНКА НА СООБЩЕНИЕ. Клиент может нажать кнопку дважды — из
пересланного сообщения, с двух вкладок, случайно. Вторая оценка
перезаписывает первую, а не добавляется: иначе один недовольный человек
накрутит статистику в любую сторону.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Feedback, Message

log = logging.getLogger(__name__)

# Значения из CHECK: больше ничего в колонку не положить.
UP = 1
DOWN = -1
SCORES = (UP, DOWN)


async def record(session: AsyncSession, message_id: int, score: int) -> Feedback | None:
    """Записать оценку сообщения. `None` — оценивать нечего или значение чужое."""
    if score not in SCORES:
        log.warning("оценка %r вне CHECK (score IN (-1, 1))", score)
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

"""Аналитика — экран 07 (приложение Б ТЗ).

ОТВЕТСТВЕННОСТЬ: пять запросов приложения Б за один поход в базу и ничего
больше. Считать в Python то, что умеет считать SQL, здесь нельзя: цифры
на защите бюджета банка должны быть воспроизводимы запросом, который
можно показать их же аналитику.

  GET /api/analytics?days=7

ЗАПРОСЫ ИЗ ТЗ ВЗЯТЫ ДОСЛОВНО, с двумя правками на весь файл:

1. `interval '7 days'` заменён на `make_interval(days => :days)` — иначе
   параметр `days` из подписи эндпоинта некуда подставить, а склеивать
   SQL строками ради числа значит открыть инъекцию на ровном месте;
2. добавлен фильтр по воркспейсу там, где в ТЗ его забыли (подзапрос
   первого сообщения диалога): в базе демо-стенда живёт не один банк.

ОЦЕНКИ КЛИЕНТОВ. Прототип обещает «4,4/5 по 380 оценкам», а в DDL раздела
5 у оценки стоит `CHECK (score IN (-1, 1))` — палец вверх или вниз.
Пятибалльную шкалу в эту колонку не положить, поэтому наружу уходит доля
довольных и число оценок. Противоречие в самом ТЗ; разбор — в шапке
`core/feedback.py`, решение за тимлидом.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dialog import get_workspace
from app.db import get_session

router = APIRouter(prefix="/api", tags=["analytics"])

# Сколько минут занимает один разговор в колл-центре. Число из прототипа:
# на нём построена вся арифметика «экономии времени», и менять его без
# банка нельзя — это их цифра, а не наша.
MINUTES_PER_CALL = 4.5

# Дольше трёх месяцев смотреть незачем: демо-стенду две недели, а на
# проде такой запрос без индекса по времени положит базу.
MAX_DAYS = 90


# Диалоги за период с разбивкой «дошёл до оператора или нет». В ТЗ это
# один процент, здесь ещё и абсолютные числа: карточка «снято с
# колл-центра» показывает штуки, а донат — доли, и считать их двумя
# запросами значит однажды показать несходящиеся цифры.
CONVERSATIONS_SQL = text(
    """
    SELECT count(*) AS total,
           count(*) FILTER (
               WHERE NOT EXISTS (
                   SELECT 1 FROM escalations e WHERE e.conversation_id = c.id
               )
           ) AS by_bot
    FROM conversations c
    WHERE c.workspace_id = :ws
      AND c.started_at > now() - make_interval(days => :days)
    """
)

# Медиана, а не среднее: один семисекундный ответ на холодной модели
# сдвигает среднее так, что норматив «< 6 сек» из раздела 3 перестаёт
# что-либо значить.
LATENCY_SQL = text(
    """
    SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) AS median
    FROM messages
    WHERE workspace_id = :ws
      AND role = 'assistant'
      AND latency_ms IS NOT NULL
      AND created_at > now() - make_interval(days => :days)
    """
)

# Канал диалога — это канал ПЕРВОГО сообщения в нём. После склейки
# контактов в одном диалоге живут два канала, и считать по всем
# сообщениям значит посчитать один разговор дважды.
CHANNELS_SQL = text(
    """
    SELECT m.channel, count(DISTINCT m.conversation_id) AS conversations
    FROM messages m
    JOIN (
        SELECT conversation_id, min(id) AS mid
        FROM messages
        WHERE workspace_id = :ws
        GROUP BY conversation_id
    ) f ON f.mid = m.id
    WHERE m.workspace_id = :ws
      AND m.created_at > now() - make_interval(days => :days)
    GROUP BY m.channel
    ORDER BY 2 DESC
    """
)

# Топ тем. Кластеризации нет и в этой версии не будет — группируем по
# первым 60 символам вопроса. Берём text_masked, а не text: в теме,
# уехавшей на экран для правления, не должно быть номера чужой карты.
TOPICS_SQL = text(
    """
    SELECT left(text_masked, 60) AS question, count(*) AS count
    FROM messages
    WHERE workspace_id = :ws
      AND role = 'user'
      AND text_masked IS NOT NULL
      AND created_at > now() - make_interval(days => :days)
    GROUP BY 1
    ORDER BY 2 DESC
    LIMIT 10
    """
)

# Язык по буквам ӣӯҳҷғқ — эвристика, и в ТЗ она названа эвристикой. Для
# дашборда достаточно: точный определитель языка на одном слове всё равно
# ошибается чаще, чем этот регэксп.
LANGUAGES_SQL = text(
    """
    SELECT CASE
             WHEN text ~ '[ӣӯҳҷғқӢӮҲҶҒҚ]' THEN 'tj'
             WHEN text ~ '[А-Яа-я]' THEN 'ru'
             ELSE 'other'
           END AS lang,
           count(*) AS messages
    FROM messages
    WHERE workspace_id = :ws
      AND role = 'user'
      AND created_at > now() - make_interval(days => :days)
    GROUP BY 1
    ORDER BY 2 DESC
    """
)

# Блок «требует внимания»: вопросы, на которых бот сдался, потому что
# ответа не нашлось в документах. Это единственная цифра на экране,
# которая говорит, что делать дальше — обновить документ.
# Оценки клиентов. В DDL раздела 5 это палец вверх или вниз
# (`CHECK score IN (-1, 1)`), поэтому наружу отдаём долю довольных, а не
# среднее по пятибалльной шкале, которую обещает прототип. Подробности
# противоречия — в шапке `core/feedback.py`.
RATING_SQL = text(
    """
    SELECT count(*) AS total,
           count(*) FILTER (WHERE score = 1) AS positive
    FROM feedback
    WHERE workspace_id = :ws
      AND created_at > now() - make_interval(days => :days)
    """
)

NO_ANSWER_SQL = text(
    """
    SELECT count(*) AS count
    FROM escalations e
    JOIN conversations c ON c.id = e.conversation_id
    WHERE c.workspace_id = :ws
      AND e.reason = 'no_answer'
      AND e.created_at > now() - make_interval(days => :days)
    """
)


@router.get("/analytics")
async def analytics(
    days: int = Query(7, ge=1, le=MAX_DAYS),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Цифры экрана 07 за последние `days` суток."""
    workspace = await get_workspace(session)
    params = {"ws": workspace.id, "days": days}

    conversations = (await session.execute(CONVERSATIONS_SQL, params)).one()
    median = (await session.execute(LATENCY_SQL, params)).scalar()
    channels = (await session.execute(CHANNELS_SQL, params)).mappings().all()
    topics = (await session.execute(TOPICS_SQL, params)).mappings().all()
    languages = (await session.execute(LANGUAGES_SQL, params)).mappings().all()
    no_answer = (await session.execute(NO_ANSWER_SQL, params)).scalar()
    rating = (await session.execute(RATING_SQL, params)).one()

    total = conversations.total or 0
    by_bot = conversations.by_bot or 0

    return {
        "days": days,
        "conversations": {
            "total": total,
            "by_bot": by_bot,
            "by_operator": total - by_bot,
            # Процент считаем здесь, а не в SQL: делить на ноль в запросе
            # пришлось бы через NULLIF, а наружу всё равно уходит число.
            "bot_share": round(100 * by_bot / total) if total else 0,
        },
        # Часы, а не минуты: на защите бюджета оперируют часами.
        "hours_saved": round(by_bot * MINUTES_PER_CALL / 60, 1),
        "median_latency_ms": int(median) if median is not None else None,
        "channels": [
            {"channel": row.channel, "conversations": row.conversations}
            for row in channels
        ],
        "languages": [
            {"lang": row.lang, "messages": row.messages} for row in languages
        ],
        "top_questions": [
            {"question": row.question, "count": row.count} for row in topics
        ],
        "attention": {"no_answer": no_answer or 0},
        "rating": {
            "total": rating.total or 0,
            "positive": rating.positive or 0,
            "share": (
                round(100 * (rating.positive or 0) / rating.total)
                if rating.total
                else None
            ),
        },
    }

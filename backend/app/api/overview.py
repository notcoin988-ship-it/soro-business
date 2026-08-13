"""Обзор — экран 01 (приложение А ТЗ).

ОТВЕТСТВЕННОСТЬ: четыре KPI с динамикой по дням и чек-лист готовности к
пилоту. Контур безопасности живёт отдельно, в `api/console.py`: он не
считается, а переключается.

  GET /api/overview

ЧЕМ ОТЛИЧАЕТСЯ ОТ ЭКРАНА 07. Аналитика отвечает на вопрос «что делали
клиенты»: разрезы по каналам, языкам, темам за произвольный период.
Обзор отвечает на «в каком состоянии стенд прямо сейчас»: те же семь
дней, но с динамикой по дням для спарклайнов и с чек-листом, которого в
аналитике нет вовсе. Два запроса из пяти общие — они импортированы, а не
скопированы: разъехавшиеся цифры на двух экранах одного продукта хуже,
чем лишний импорт.

ЧЕК-ЛИСТ СЧИТАЕТСЯ, А НЕ РИСУЕТСЯ. В прототипе все пять пунктов зашиты
галочками, включая «4 источника, 1 248 фрагментов». Это первый экран,
который видит правление банка, и галочка напротив ненастроенного канала —
худшее, что на нём может быть.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.analytics import CONVERSATIONS_SQL, LATENCY_SQL
from app.config import settings
from app.core.dialog import get_workspace
from app.db import get_session

router = APIRouter(prefix="/api", tags=["overview"])

# Окно экрана 01 фиксировано: «Диалогов за 7 дней» написано прямо на
# карточке, и делать его настраиваемым значит рассинхронизировать подпись
# с числом.
DAYS = 7


# Динамика по дням — для спарклайнов. Дни без диалогов должны остаться в
# ряду нулями, иначе линия соврёт: провал превратится в ровный участок.
# Отсюда generate_series слева, а не GROUP BY по факту.
DAILY_CONVERSATIONS_SQL = text(
    """
    SELECT d::date AS day,
           count(c.id) AS total,
           count(c.id) FILTER (
               WHERE NOT EXISTS (
                   SELECT 1 FROM escalations e WHERE e.conversation_id = c.id
               )
           ) AS by_bot
    FROM generate_series(
             date_trunc('day', now()) - make_interval(days => :days - 1),
             date_trunc('day', now()),
             interval '1 day'
         ) AS d
    LEFT JOIN conversations c
           ON c.workspace_id = :ws
          AND c.started_at >= d
          AND c.started_at < d + interval '1 day'
    GROUP BY 1
    ORDER BY 1
    """
)

# Ответы бота: сколько их и у скольких есть ссылка на источник. Ссылка —
# это непустой `chunks_used`: именно из него канал рисует бейджи, и если
# он пуст, показывать клиенту нечего.
CITED_SQL = text(
    """
    SELECT count(*) AS answers,
           count(*) FILTER (WHERE cardinality(chunks_used) > 0) AS cited
    FROM messages
    WHERE workspace_id = :ws
      AND role = 'assistant'
      AND created_at > now() - make_interval(days => :days)
    """
)

DAILY_CITED_SQL = text(
    """
    SELECT d::date AS day,
           count(m.id) AS answers,
           count(m.id) FILTER (WHERE cardinality(m.chunks_used) > 0) AS cited,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY m.latency_ms) AS median
    FROM generate_series(
             date_trunc('day', now()) - make_interval(days => :days - 1),
             date_trunc('day', now()),
             interval '1 day'
         ) AS d
    LEFT JOIN messages m
           ON m.workspace_id = :ws
          AND m.role = 'assistant'
          AND m.created_at >= d
          AND m.created_at < d + interval '1 day'
    GROUP BY 1
    ORDER BY 1
    """
)

# 95-й перцентиль стоит рядом с медианой на той же карточке: медиана
# говорит, как отвечает бот обычно, а 95-й — как он отвечает в худшие
# минуты, и на приёмке спрашивают именно про них.
PERCENTILE_SQL = text(
    """
    SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95
    FROM messages
    WHERE workspace_id = :ws
      AND role = 'assistant'
      AND latency_ms IS NOT NULL
      AND created_at > now() - make_interval(days => :days)
    """
)

CHANNELS_SQL = text(
    """
    SELECT DISTINCT channel
    FROM messages
    WHERE workspace_id = :ws
      AND created_at > now() - make_interval(days => :days)
    """
)

DOCUMENTS_SQL = text(
    """
    SELECT count(*) FILTER (WHERE d.status = 'ready') AS ready,
           count(*) AS total,
           (SELECT count(*) FROM chunks WHERE workspace_id = :ws) AS chunks
    FROM documents d
    WHERE d.workspace_id = :ws
    """
)

CHANNEL_NAMES = {"telegram": "Telegram", "widget": "веб", "whatsapp": "WhatsApp"}


@router.get("/overview")
async def overview(session: AsyncSession = Depends(get_session)) -> dict:
    """Всё, что показывает экран 01, кроме переключателей безопасности."""
    workspace = await get_workspace(session)
    params = {"ws": workspace.id, "days": DAYS}

    conversations = (await session.execute(CONVERSATIONS_SQL, params)).one()
    daily_conversations = (
        (await session.execute(DAILY_CONVERSATIONS_SQL, params)).mappings().all()
    )
    median = (await session.execute(LATENCY_SQL, params)).scalar()
    p95 = (await session.execute(PERCENTILE_SQL, params)).scalar()
    cited = (await session.execute(CITED_SQL, params)).one()
    daily_cited = (await session.execute(DAILY_CITED_SQL, params)).mappings().all()
    channels = (await session.execute(CHANNELS_SQL, params)).scalars().all()
    documents = (await session.execute(DOCUMENTS_SQL, params)).one()

    total = conversations.total or 0
    by_bot = conversations.by_bot or 0
    answers = cited.answers or 0

    return {
        "days": DAYS,
        "conversations": {
            "total": total,
            "by_bot": by_bot,
            "bot_share": round(100 * by_bot / total) if total else 0,
            # Подпись под первой карточкой: какие каналы реально писали.
            "channels": [CHANNEL_NAMES.get(name, name) for name in sorted(channels)],
            "spark": [row["total"] for row in daily_conversations],
            "spark_bot_share": [
                round(100 * row["by_bot"] / row["total"]) if row["total"] else 0
                for row in daily_conversations
            ],
        },
        "latency": {
            "median_ms": int(median) if median is not None else None,
            "p95_ms": int(p95) if p95 is not None else None,
            "spark": [
                int(row["median"]) if row["median"] is not None else 0
                for row in daily_cited
            ],
        },
        "citations": {
            "answers": answers,
            "cited": cited.cited or 0,
            "share": round(100 * (cited.cited or 0) / answers) if answers else 0,
            "spark": [
                round(100 * row["cited"] / row["answers"]) if row["answers"] else 0
                for row in daily_cited
            ],
        },
        "readiness": _readiness(documents),
    }


# Плейсхолдеры в `.env`. Живой прогон показал, зачем это нужно: в файле
# стенда лежит `WHATSAPP_TOKEN=ЗАПРОСИ-У-ТИМЛИДА`, и обычная проверка «не
# пусто» ставила галочку напротив неподключённого канала — ровно то, чего
# на первом экране быть не должно.
#
# Правило простое и не требует списка: настоящий токен, id и адрес не
# содержат кириллицы и не кончаются многоточием. `.env.example` весь такой
# по построению — «EAAG...», «123456:ABC...», «поменять-обязательно».
_PLACEHOLDER_RE = re.compile(r"[А-Яа-яЁё]")


def _filled(value: str | None) -> bool:
    """Задано ли значение по-настоящему, а не «запросить у тимлида»."""
    value = (value or "").strip()
    if not value or value.endswith("..."):
        return False
    return not _PLACEHOLDER_RE.search(value)


def _readiness(documents) -> list[dict]:
    """Чек-лист готовности к пилоту — по факту, а не по прототипу.

    Последний пункт единственный статический: интеграции с CRM банка нет
    ни в коде, ни в ТЗ этой версии, и вычислять там нечего.
    """
    ready = documents.ready or 0
    chunks = documents.chunks or 0
    telegram = _filled(settings.TELEGRAM_BOT_TOKEN)
    whatsapp = _filled(settings.WHATSAPP_TOKEN) and _filled(settings.WHATSAPP_PHONE_ID)
    public = _filled(settings.PUBLIC_BASE_URL)
    host = settings.PUBLIC_BASE_URL.rstrip("/")

    return [
        {
            "title": "Документы загружены",
            "done": ready > 0,
            "hint": (
                f"{ready} источников, {chunks} фрагментов"
                if ready
                else "база знаний пуста — экран 02"
            ),
        },
        {
            "title": "Telegram-бот подключён",
            "done": telegram,
            "hint": (
                f"@{settings.TELEGRAM_BOT_USERNAME}, отвечает на тадж. и рус."
                if telegram
                else "TELEGRAM_BOT_TOKEN не задан в .env"
            ),
        },
        {
            "title": "Веб-виджет выдан",
            # Сам виджет готов всегда — его отдаёт бэкенд. Но выдавать
            # банку нечего, пока нет публичного адреса: сниппет со
            # строкой «ЗАПОЛНИТЬ-после-ngrok» вставить некуда.
            "done": public,
            "hint": (
                f"скрипт для вставки: {host}/w.js"
                if public
                else "PUBLIC_BASE_URL не задан — поднимите ngrok"
            ),
        },
        {
            "title": "WhatsApp Business API",
            "done": whatsapp,
            "hint": (
                "песочница Meta подключена"
                if whatsapp
                else "канал не подключён: нет токена и номера в .env"
            ),
        },
        {
            "title": "Передача в колл-центр",
            "done": False,
            "hint": "интеграция с вашей CRM — обсуждается на этапе пилота",
        },
    ]

"""Отчёт словами: «нужен отчёт за эту неделю», «а по июню?».

ОТВЕТСТВЕННОСТЬ: превратить фразу владельца банка в ПЕРИОД, посчитать по
этому периоду те же цифры, что показывает экран 07, и дать модели
пересказать их человеческим текстом. Один и тот же путь обслуживает
экран 08 консоли и Telegram — канал только доставляет вопрос и ответ.

ГЛАВНОЕ ПРАВИЛО, из которого следует вся структура файла: ПЕРИОД И ЦИФРЫ
СЧИТАЕТ КОД, МОДЕЛЬ ТОЛЬКО ПЕРЕСКАЗЫВАЕТ. Соблазн отдать разбор фразы
модели («она же поймёт „за прошлый месяц"») очень велик и обходится
дорого:

  * первый прогон на сырых числах: в JSON уехало `median_latency_ms: 900`,
    модель написала «среднее время ответа — 900 мс». Медиана и среднее —
    разные величины, и на защите бюджета такую подмену заметят первой;
  * период, определённый моделью, невоспроизводим: на один и тот же
    вопрос в двух прогонах приходят разные границы, а владелец сверяет
    цифру с экраном 07 и видит расхождение.

Поэтому модель получает готовую сводку с русскими подписями
(`format_facts`) и не имеет доступа ни к базе, ни к арифметике. Ту же
сводку показывает экран 08 — рядом с ответом, чтобы видеть, из чего он
собран.

ОТКУДА ЦИФРЫ. Запросы приложения Б ТЗ переехали сюда из `api/analytics.py`
и приняли границы `:since`/`:until` вместо `interval '7 days'`. Причина:
отчёт спрашивают за произвольный период («июнь»), а цифра на экране 07 и
цифра в отчёте за те же дни обязаны совпадать — значит запрос должен быть
ОДИН. `api/analytics.py` теперь зовёт `collect` с окном «последние N
суток» и получает ровно то, что получал раньше.

ЧАСОВОЙ ПОЯС. Календарные периоды («эта неделя», «июнь») считаются по
душанбинскому времени: владелец банка сверяет отчёт со своим календарём, а
не с UTC сервера. Скользящие окна («за 7 дней») считаются от `now` — так
же, как их считает экран 07, иначе те же семь дней дали бы два разных
числа.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import audit, llm, phrases
from app.models import Workspace

log = logging.getLogger(__name__)

# Таджикистан живёт в UTC+5 и переходов на летнее время не делает — поэтому
# фиксированное смещение, а не ZoneInfo: тащить в образ базу tzdata ради
# одного часового пояса без DST незачем.
DUSHANBE = timezone(timedelta(hours=5), "Asia/Dushanbe")

# Сколько минут занимает один разговор в колл-центре. Число из прототипа:
# на нём построена вся арифметика «экономии времени», и менять его без
# банка нельзя — это их цифра, а не наша.
MINUTES_PER_CALL = 4.5

# Потолок на глубину отчёта. У `messages` индекс только по
# (conversation_id, created_at) — запрос за произвольный период идёт
# сканированием по воркспейсу, и на проде год данных читать не надо.
# Год с запасом на високосный: «за прошлый декабрь» в январе должен
# помещаться.
MAX_RANGE_DAYS = 366

# Период по умолчанию, когда в вопросе его не назвали вовсе. Семь дней, как
# на экранах 01 и 07: владелец сравнивает с тем, что видел там.
DEFAULT_DAYS = 7

# Отчёт короткий по природе: 4–8 строк текста. Потолок из `.env` (2000)
# здесь не нужен и только даёт модели место расписаться на полторы страницы.
REPORT_MAX_TOKENS = 700

MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
MONTHS_NOMINATIVE = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)


# ---------------------------------------------------------------------------
# период
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Period:
    """Границы отчёта и их человеческое название.

    `since` включительно, `until` исключительно — иначе сообщение,
    пришедшее ровно в полночь, попало бы в оба соседних месяца.

    `assumed` значит «период в вопросе не назвали, взяли по умолчанию». Это
    не деталь реализации: ответ обязан сказать, за что он, иначе владелец
    прочитает цифры за семь дней как цифры за месяц.
    """

    name: str
    since: datetime
    until: datetime
    assumed: bool = False

    @property
    def days(self) -> int:
        """Длина периода в сутках, округлённая вверх. Для подписи и потолков."""
        seconds = (self.until - self.since).total_seconds()
        return max(1, int((seconds + 86399) // 86400))

    @property
    def title(self) -> str:
        """«эта неделя · 11–17 августа 2026» — название и точные даты."""
        dates = human_range(self.since, self.until)
        return dates if dates == self.name else f"{self.name} · {dates}"


def human_range(since: datetime, until: datetime) -> str:
    """Границы периода по-русски. `until` исключительна, поэтому последний
    день — это `until` минус секунда: «по 17 августа», а не «по 18-е»."""
    first = since.astimezone(DUSHANBE)
    last = (until - timedelta(seconds=1)).astimezone(DUSHANBE)

    if first.date() == last.date():
        return f"{first.day} {MONTHS_GENITIVE[first.month - 1]} {first.year}"
    if (first.year, first.month) == (last.year, last.month):
        return (
            f"{first.day}–{last.day} {MONTHS_GENITIVE[first.month - 1]} {first.year}"
        )
    if first.year == last.year:
        return (
            f"{first.day} {MONTHS_GENITIVE[first.month - 1]} — "
            f"{last.day} {MONTHS_GENITIVE[last.month - 1]} {first.year}"
        )
    return (
        f"{first.day} {MONTHS_GENITIVE[first.month - 1]} {first.year} — "
        f"{last.day} {MONTHS_GENITIVE[last.month - 1]} {last.year}"
    )


def _local_midnight(moment: datetime) -> datetime:
    """Начало суток по душанбинскому времени."""
    local = moment.astimezone(DUSHANBE)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def _month_start(year: int, month: int) -> datetime:
    return datetime(year, month, 1, tzinfo=DUSHANBE)


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _rolling(now: datetime, days: int, name: str) -> Period:
    """Скользящее окно «последние N суток» — как на экране 07."""
    days = max(1, min(days, MAX_RANGE_DAYS))
    return Period(name=name, since=now - timedelta(days=days), until=now)


def rolling_period(days: int, now: datetime | None = None) -> Period:
    """Окно «последние N суток» для экранов 01, 05 и 07.

    Живёт здесь, а не в трёх файлах api/: все три экрана обязаны считать
    одно и то же окно одинаково, иначе «диалогов за 7 дней» на обзоре и на
    аналитике разойдутся, и объяснять это придётся банку.
    """
    return _rolling(now or datetime.now(tz=timezone.utc), days, f"последние {days} дней")


def bounds(period: Period) -> dict:
    """Границы периода как параметры для SQL этого модуля."""
    return {"since": period.since, "until": period.until}


# Названия месяцев в обоих языках. Стебель, а не полное слово: «июнь»,
# «июня», «в июне», таджикское «июн» — одна и та же строка для нас. Стебли
# выбраны так, чтобы не находиться внутри других слов: «март», а не «мар»
# (иначе «маркетинг»), «апрел», а не «апр».
MONTH_PATTERNS = (
    (1, r"январ|янв\b"),
    (2, r"феврал|февр\b|фев\b"),
    (3, r"март"),
    (4, r"апрел|апр\b"),
    # «май», «мая», «мае», таджикское «маи» — и ничего больше: «маи» внутри
    # слова не ловим, поэтому граница обязательна с обеих сторон.
    (5, r"\bма[йяеи]\b"),
    (6, r"июн"),
    (7, r"июл"),
    (8, r"август|авг\b"),
    (9, r"сентябр|сент\b|сен\b"),
    (10, r"октябр|окт\b"),
    (11, r"ноябр|нояб\b|ноя\b"),
    (12, r"декабр|дек\b"),
)

# Слова «прошлый» и «этот» в двух языках. Собраны в одном месте: они
# участвуют в правилах и про неделю, и про месяц.
_PREV = r"прошл\w*|прошедш\w*|предыдущ\w*|минувш\w*|гузашт\w*|пеш\w*"
_THIS = r"эт\w*|текущ\w*|нынешн\w*|ҷор[ӣи]|ин\b|ҳозира|хозира"

_WEEK = r"недел\w*|ҳафта\w*|хафта\w*"
_MONTH_WORD = r"месяц\w*|мо[ҳх]\w*"

# Числа словами: «за две недели», «за три месяца». Руководитель пишет их так
# не реже, чем цифрами, а до шести хватает — «за семь недель» никто не
# просит, для длинных периодов есть месяцы по имени.
WORD_NUMBERS = {
    "два": 2, "две": 2, "ду": 2,
    "три": 3, "се": 3,
    "четыре": 4, "чор": 4,
    "пять": 5, "панҷ": 5, "панч": 5,
    "шесть": 6, "шаш": 6,
}
_COUNT = "|".join([r"\d+", *WORD_NUMBERS])


def _count(token: str) -> int:
    return int(token) if token.isdigit() else WORD_NUMBERS[token.lower()]


def parse_period(question: str, now: datetime | None = None) -> Period:
    """Фраза владельца → границы отчёта.

    Разбор механический и это осознанный выбор: см. шапку модуля. Порядок
    правил важен — «прошлая неделя» обязана проверяться раньше «недели»,
    иначе она станет скользящими семью днями.

    Период не распознан — возвращаем семь дней с `assumed=True`, а не
    ошибку: владелец написал «как у нас дела?», и ответ «не понял период»
    хуже, чем ответ за неделю с честной оговоркой.
    """
    now = now or datetime.now(tz=timezone.utc)
    text_lower = (question or "").lower()
    today = _local_midnight(now)

    # --- сутки ---
    if re.search(r"\bсегодня\b|\bимр[ӯу]з\b", text_lower):
        return Period("сегодня", today, now)
    if re.search(r"\bвчера\b|\bдир[ӯу]з\b", text_lower):
        return Period("вчера", today - timedelta(days=1), today)

    # --- недели ---
    monday = today - timedelta(days=today.weekday())
    if re.search(rf"({_PREV})\s+({_WEEK})|({_WEEK})\w*\s+({_PREV})", text_lower):
        return Period("прошлая неделя", monday - timedelta(days=7), monday)
    if re.search(rf"({_THIS})\s+({_WEEK})|({_WEEK})\w*\s+({_THIS})", text_lower):
        return Period("эта неделя", monday, now)

    # --- месяцы ---
    if re.search(rf"({_PREV})\s+({_MONTH_WORD})|({_MONTH_WORD})\s+({_PREV})", text_lower):
        first_this = _month_start(today.year, today.month)
        previous = first_this - timedelta(days=1)
        start = _month_start(previous.year, previous.month)
        return Period(f"{MONTHS_NOMINATIVE[start.month - 1]} {start.year}", start, first_this)
    if re.search(rf"({_THIS})\s+({_MONTH_WORD})|({_MONTH_WORD})\s+({_THIS})", text_lower):
        start = _month_start(today.year, today.month)
        return Period(
            f"{MONTHS_NOMINATIVE[start.month - 1]} {start.year}, с начала месяца",
            start,
            now,
        )

    # --- месяц по имени, с годом или без ---
    named = _named_month(text_lower, today)
    if named is not None:
        return named

    # --- скользящие окна ---
    window = _rolling_window(text_lower, now)
    if window is not None:
        return window

    return Period(
        f"последние {DEFAULT_DAYS} дней",
        now - timedelta(days=DEFAULT_DAYS),
        now,
        assumed=True,
    )


def _named_month(text_lower: str, today: datetime) -> Period | None:
    """«за июнь», «в июне 2025», таджикское «июн»."""
    for number, pattern in MONTH_PATTERNS:
        if not re.search(pattern, text_lower):
            continue

        year_match = re.search(r"\b(20\d{2})\b", text_lower)
        if year_match:
            year = int(year_match.group(1))
        else:
            # Год не назвали — берём ПОСЛЕДНИЙ такой месяц, который уже
            # начался. В августе «декабрь» — это прошлогодний декабрь, а не
            # тот, что ещё не наступил: отчёт за будущее не бывает.
            year = today.year if number <= today.month else today.year - 1

        start = _month_start(year, number)
        end = _month_start(*_next_month(year, number))
        return Period(f"{MONTHS_NOMINATIVE[number - 1]} {year}", start, end)
    return None


def _rolling_window(text_lower: str, now: datetime) -> Period | None:
    """«за 7 дней», «за две недели», «за три месяца», «за год»."""
    days_match = re.search(rf"({_COUNT})\s*(дн\w*|сут\w*|р[ӯу]з\w*)", text_lower)
    if days_match:
        days = _count(days_match.group(1))
        return _rolling(now, days, f"последние {min(days, MAX_RANGE_DAYS)} дней")

    weeks_match = re.search(rf"({_COUNT})\s*({_WEEK})", text_lower)
    if weeks_match:
        weeks = _count(weeks_match.group(1))
        return _rolling(now, weeks * 7, f"последние {weeks * 7} дней")

    months_match = re.search(rf"({_COUNT})\s*({_MONTH_WORD})", text_lower)
    if months_match:
        # 30 суток на месяц: это скользящее окно, а не календарь. Календарный
        # месяц спрашивают по имени («за июнь») и он разбирается выше.
        months = _count(months_match.group(1))
        return _rolling(now, months * 30, f"последние {months * 30} дней")

    if re.search(rf"\bполгода\b|\bшесть\s+({_MONTH_WORD})", text_lower):
        return _rolling(now, 180, "последние 180 дней")
    if re.search(r"\bза год\b|\bгод\w*\b|\bсол\w*\b", text_lower):
        return _rolling(now, 365, "последние 365 дней")
    if re.search(rf"\b({_WEEK})\b", text_lower):
        return _rolling(now, 7, "последние 7 дней")
    if re.search(rf"\b({_MONTH_WORD})\b", text_lower):
        return _rolling(now, 30, "последние 30 дней")
    return None


# ---------------------------------------------------------------------------
# цифры
# ---------------------------------------------------------------------------
#
# ЗАПРОСЫ ИЗ ПРИЛОЖЕНИЯ Б ТЗ, дословно, с тремя правками на весь блок:
#
# 1. `interval '7 days'` заменён на границы `:since`/`:until` — иначе
#    отчёт за июнь не выразить, а склеивать SQL строками ради даты значит
#    открыть инъекцию на ровном месте;
# 2. добавлен фильтр по воркспейсу там, где в ТЗ его забыли (подзапрос
#    первого сообщения диалога): в базе демо-стенда живёт не один банк;
# 3. `>= :since AND < :until` вместо `>` — период полуоткрыт, см. `Period`.

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
      AND c.started_at >= :since
      AND c.started_at < :until
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
      AND created_at >= :since
      AND created_at < :until
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
      AND m.created_at >= :since
      AND m.created_at < :until
    GROUP BY m.channel
    ORDER BY 2 DESC
    """
)

# Топ тем. Кластеризации нет и в этой версии не будет — группируем по
# первым 60 символам вопроса. Берём text_masked, а не text: в теме,
# уехавшей в отчёт для правления, не должно быть номера чужой карты.
TOPICS_SQL = text(
    """
    SELECT left(text_masked, 60) AS question, count(*) AS count
    FROM messages
    WHERE workspace_id = :ws
      AND role = 'user'
      AND text_masked IS NOT NULL
      AND created_at >= :since
      AND created_at < :until
    GROUP BY 1
    ORDER BY 2 DESC
    LIMIT 10
    """
)

# ЯЗЫК: БУКВЫ ПЛЮС СЛОВА. В ТЗ эвристика — только буквы ӣӯҳҷғқ, и на
# живых данных она провалилась ровно там, где важнее всего: экран,
# который продаёт таджикоязычного бота, показывал `ru: 9, tj: 0`, хотя
# половина вопросов таджикские. Эти буквы есть далеко не в каждой фразе:
#
#   «Фоизи амонати Ояндасоз чанд аст?»   — ни одной, считалось русским
#   «салом мехохам кредить гирам»        — ни одной, считалось русским
#   «Мӯҳлаташ чанд сол?»                 — есть, считалось таджикским
#
# Причина простая: клиенты пишут без диакритики, с телефонной раскладки.
# Поэтому добавлен список служебных слов, которые в русском тексте не
# встречаются вовсе: «аст», «чанд», «мехоҳам/мехохам», «кунам» и прочие.
# Слова именно служебные, а не банковские: «фоиз» или «қарз» могут
# оказаться в русской фразе как термин, а «аст» — нет.
#
# Слова, которых здесь нет намеренно: «дар» (в русском это подарок),
# «ман», «бо», «ки» — короткие и совпадают с обрывками русских слов.
# Граница `\y` (в PostgreSQL это граница слова) обязательна: без неё
# «аст» найдётся внутри «пласт» и «участие».
TAJIK_WORDS = (
    "аст|чанд|чӣ|чи гуна|чигуна|мехоҳам|мехохам|мехоҳед|мехохед|"
    "кунам|кунед|кушодан|дорад|доред|медиҳад|медихад|медиҳед|"
    "шумо|барои|ҳисоб|хисоб|салом|раҳмат|рахмат|лутфан|оё|кадом|"
    "гирифтан|гирам|ниёз|метавонам|метавонед|бошад|ҳастам|хастам"
)

LANGUAGES_SQL = text(
    f"""
    SELECT CASE
             WHEN text ~ '[ӣӯҳҷғқӢӮҲҶҒҚ]' THEN 'tj'
             WHEN text ~* '\\y({TAJIK_WORDS})\\y' THEN 'tj'
             WHEN text ~ '[А-Яа-я]' THEN 'ru'
             ELSE 'other'
           END AS lang,
           count(*) AS messages
    FROM messages
    WHERE workspace_id = :ws
      AND role = 'user'
      AND created_at >= :since
      AND created_at < :until
    GROUP BY 1
    ORDER BY 2 DESC
    """
)

# Оценки клиентов — пять звёзд (миграция 0003). Среднее округляем до
# десятой: карточка прототипа обещает «4,4/5», и вторая цифра после
# запятой на ней просто не поместится.
RATING_SQL = text(
    """
    SELECT count(*) AS total,
           avg(score)::numeric(3, 1) AS average
    FROM feedback
    WHERE workspace_id = :ws
      AND created_at >= :since
      AND created_at < :until
    """
)

# Блок «требует внимания»: вопросы, на которых бот сдался, потому что
# ответа не нашлось в документах. Это единственная цифра в отчёте,
# которая говорит, что делать дальше — обновить документ.
NO_ANSWER_SQL = text(
    """
    SELECT count(*) AS count
    FROM escalations e
    JOIN conversations c ON c.id = e.conversation_id
    WHERE c.workspace_id = :ws
      AND e.reason = 'no_answer'
      AND e.created_at >= :since
      AND e.created_at < :until
    """
)


async def collect(session: AsyncSession, workspace_id: int, period: Period) -> dict:
    """Цифры приложения Б за произвольный период — один поход в базу.

    Форма ответа та же, что отдаёт `GET /api/analytics`: этот же словарь
    уходит и на экран 07, и в сводку для модели. Две формы одних и тех же
    чисел однажды разъедутся, одна — нет.
    """
    params = {"ws": workspace_id, "since": period.since, "until": period.until}

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
            "average": float(rating.average) if rating.average is not None else None,
        },
    }


# ---------------------------------------------------------------------------
# сводка для модели (и для экрана)
# ---------------------------------------------------------------------------

CHANNEL_NAMES = {
    "telegram": "Telegram",
    "widget": "веб-виджет",
    "whatsapp": "WhatsApp",
}
LANGUAGE_NAMES = {"tj": "таджикский", "ru": "русский", "other": "другой"}


def _number(value: float) -> str:
    """Русская запись числа: запятая, без хвостового нуля у целых."""
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}".replace(".", ",")


def format_facts(data: dict, period: Period) -> str:
    """Сводка с русскими подписями — единственный источник чисел для модели.

    ПОДПИСИ ВАЖНЕЕ ЗНАЧЕНИЙ. Первый прогон отдавал модели сырой JSON, и она
    честно переписала ключ `median_latency_ms` словом «среднее». Здесь
    величина названа так, как её нужно назвать в отчёте, — и правило 3
    системного промпта запрещает переименовывать.

    Строки, которых нет, не пишем вовсе: «Оценка клиентов: нет данных» в
    сводке превращается в абзац про отсутствие оценок в ответе, а владелец
    спрашивал не об этом.
    """
    talks = data["conversations"]
    lines = [
        f"Период: {period.title}",
        f"Обращений всего: {talks['total']}",
    ]

    if talks["total"]:
        lines.append(
            f"Бот закрыл сам, без оператора: {talks['by_bot']} "
            f"({talks['bot_share']}% обращений)"
        )
        lines.append(f"Дошло до оператора: {talks['by_operator']}")
        lines.append(
            f"Сэкономлено времени колл-центра: {_number(data['hours_saved'])} ч "
            f"(по {_number(MINUTES_PER_CALL)} мин на разговор)"
        )

    if data["median_latency_ms"] is not None:
        seconds = data["median_latency_ms"] / 1000
        lines.append(
            f"Медиана времени ответа бота: {_number(round(seconds, 1))} с "
            "(норматив приёмки — меньше 6 с)"
        )

    if data["channels"]:
        parts = ", ".join(
            f"{CHANNEL_NAMES.get(row['channel'], row['channel'])} — "
            f"{row['conversations']}"
            for row in data["channels"]
        )
        lines.append(f"Обращения по каналам: {parts}")

    if data["languages"]:
        parts = ", ".join(
            f"{LANGUAGE_NAMES.get(row['lang'], row['lang'])} — {row['messages']}"
            for row in data["languages"]
        )
        lines.append(f"Язык вопросов клиентов (сообщений): {parts}")

    if data["rating"]["average"] is not None:
        lines.append(
            f"Оценка клиентов: {_number(data['rating']['average'])} из 5 "
            f"(оценок: {data['rating']['total']})"
        )

    # ФОРМУЛИРОВКА БЕЗ ДВОЙНОГО ОТРИЦАНИЯ. Первый вариант читался как «Бот
    # не нашёл ответа в документах: 0 раз» — и модель на живом прогоне
    # честно написала «бот не смог найти ответы в документах», то есть
    # прочитала ноль наизнанку. Подпись переставлена так, чтобы отрицание
    # относилось к причине эскалации, а не к самому боту.
    lines.append(
        "Эскалаций с причиной «ответа нет в документах»: "
        f"{data['attention']['no_answer']}"
    )

    if data["top_questions"]:
        # Пять, а не десять: в отчёт из четырёх абзацев больше не влезет, а
        # модель, получив десять тем, начинает перечислять их все.
        parts = "; ".join(
            f"«{row['question']}» — {row['count']}"
            for row in data["top_questions"][:5]
        )
        lines.append(f"Частые вопросы: {parts}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# пересказ моделью
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Ту — Soro, аналитики «{bank_name}».
Ты пишешь короткий отчёт для руководителя банка.
Правила (нарушать НЕЛЬЗЯ):
1. Единственный источник чисел — блок <данные>. Числа, которого там
   нет, в отчёте быть не может.
2. Ничего не досчитывай сам: проценты, доли, часы и медиана уже
   посчитаны. Не хватает величины — так и скажи одной фразой.
3. Называй величины ровно так, как они названы в блоке: медиана — это
   медиана, а не среднее.
4. Первая строка — период, тем же названием, что в блоке.
5. НЕ ПЕРЕПИСЫВАЙ блок построчно: руководитель видит его рядом с твоим
   отчётом. Напиши 3–6 предложений связного текста: сперва сколько было
   обращений и сколько из них бот закрыл сам, потом одно-два наблюдения
   о том, что требует внимания.
6. Ноль значит «ни разу». «0 раз» — это хорошая новость, а не поломка:
   писать «бот не смог» или «ответов не нашлось» в таком случае нельзя.
7. Пиши обычным текстом. Никакой разметки: без **, ##, без нумерованных
   списков. Перечисление — тире в начале строки.
8. Если обращений за период не было — скажи об этом одним предложением
   и не придумывай причин.
9. Язык отчёта задаёт вопрос руководителя: русский вопрос — русский
   ответ, таджикский — таджикский.
10. Не давай советов про банковские продукты и ничего не обещай. Твоя
   работа — цифры и один короткий вывод по ним.

Пример. Числа в примере ЧУЖИЕ, из другого банка, — в свой отчёт их не
переносить, они здесь только чтобы показать форму.
<данные>
Период: прошлая неделя · 3–9 августа 2026
Обращений всего: 128
Бот закрыл сам, без оператора: 97 (76% обращений)
Дошло до оператора: 31
Сэкономлено времени колл-центра: 7,3 ч (по 4,5 мин на разговор)
Медиана времени ответа бота: 1,4 с (норматив приёмки — меньше 6 с)
Обращения по каналам: Telegram — 80, веб-виджет — 48
Эскалаций с причиной «ответа нет в документах»: 12
</данные>
Отчёт:
Прошлая неделя · 3–9 августа 2026

За неделю пришло 128 обращений, 97 из них бот закрыл сам — это 76% и
примерно 7,3 часа работы колл-центра. Отвечал быстро: медиана 1,4
секунды против норматива в 6. К оператору ушло 31 обращение, и 12 из
них — потому что ответа не нашлось в документах; это и есть очередь на
пополнение базы знаний. Больше половины обращений пришло из Telegram."""

# Пустой период модели не отдаём вовсе. Живой прогон на июне (обращений
# ноль) дал зацикливание: «Доля ответов… не посчитана, так как обращений не
# было» повторилось двадцать три раза, пока не кончились токены. Правило
# «скажи одним предложением» тут не спасает — пересказывать нечего, и
# маленькая модель начинает перебирать величины. Текст фиксирован: он
# короче, честнее и не может сломаться.
EMPTY_TEXT = {
    "ru": "{title}\n\nЗа этот период обращений не было — ни одного диалога "
          "ни в Telegram, ни в веб-виджете. Если это неожиданно, проверьте "
          "на экране 05, что каналы подключены, и назовите период иначе: "
          "«за прошлый месяц», «за июнь».",
    "tg": "{title}\n\nДар ин давра ҳеҷ муроҷиат сабт нашудааст — на дар "
          "Telegram, на дар виҷети сайт. Агар ин ғайричашмдошт бошад, дар "
          "экрани 05 пайвасти каналҳоро санҷед ва давраро дигар номбар "
          "кунед: «моҳи гузашта», «июн»."
}

# Прямое указание языка — тем же приёмом, что в `llm.LANGUAGE_ORDER`:
# строка написана на самом языке и служит модели образцом. Формулировки
# свои: там речь про клиента, здесь про руководителя банка.
LANGUAGE_ORDER = {
    "ru": "ЯЗЫК ОТЧЁТА: русский. Вопрос задан по-русски — отвечай только "
          "по-русски, таджикский вариант не добавляй.",
    "tg": "ЗАБОНИ ҲИСОБОТ: тоҷикӣ. Савол бо тоҷикӣ дода шуд — танҳо бо "
          "тоҷикӣ ҷавоб деҳ, варианти русиро илова накун.",
}

USER_TEMPLATE = """<данные>
{facts}
</данные>

Вопрос руководителя: {question}"""

# Разметку модель всё равно иногда ставит — правило 6 просьба, а не
# гарантия. Вырезаем механически: в Telegram `parse_mode=None`, и «**»
# уедет клиенту как есть.
MARKDOWN_RE = re.compile(r"\*{1,3}|_{2,3}|`+|^#{1,6}\s*", re.MULTILINE)

# Что отвечаем, когда модель недоступна. Цифры к этому моменту уже
# посчитаны, и отдать сводку без пересказа — куда лучше, чем ошибку:
# владелец получит те же числа, только без человеческого текста.
MODEL_DOWN_NOTE = "Модель сейчас недоступна, поэтому — сводка цифрами, как есть."


def build_messages(question: str, facts: str, bank_name: str) -> list[dict]:
    system = SYSTEM_PROMPT.format(bank_name=bank_name)
    language = phrases.detect_language(question)
    if language:
        system += "\n" + LANGUAGE_ORDER[language]
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": USER_TEMPLATE.format(facts=facts, question=question)},
    ]


def strip_markdown(raw: str) -> str:
    """Убрать разметку, которую модель поставила против правила 7."""
    return MARKDOWN_RE.sub("", raw or "").strip()


def collapse_repeats(text: str, limit: int = 2) -> str:
    """Оборвать текст на строке, которая уже была `limit` раз.

    СТОРОЖ ПРОТИВ ЗАЦИКЛИВАНИЯ. Промпт и пример его почти лечат, но
    «почти» — не гарантия: на пустом периоде модель повторила одну фразу
    двадцать три раза, пока не кончились токены, и такое уедет
    руководителю целиком. Обрезаем по первому повтору сверх лимита:
    начало отчёта осмысленно, и лучше короткий отчёт, чем простыня.

    Лимит два, а не один: строки вида «— Telegram: 12» законно похожи, а
    полностью совпадающая строка дважды в шестистрочном отчёте — уже
    подозрительно, но ещё не поломка.
    """
    seen: dict[str, int] = {}
    kept: list[str] = []
    for line in (text or "").splitlines():
        key = line.strip().lower()
        if key:
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > limit:
                break
        kept.append(line)
    return "\n".join(kept).strip()


@dataclass
class Report:
    """Готовый отчёт: период, цифры, сводка и текст."""

    period: Period
    data: dict
    facts: str
    text: str
    latency_ms: int = 0
    # модель не ответила, текст собран из сводки
    degraded: bool = False
    warnings: list[str] = field(default_factory=list)


async def narrate(question: str, facts: str, *, bank_name: str, client=None) -> str:
    """Пересказ сводки человеческим текстом. Пустая строка — модель не ответила.

    ОДНА ПОВТОРНАЯ ПОПЫТКА на обрыв соединения. Живой прогон: из ~20 вызовов
    два упали с `ConnectError` за 30–45 мс, то есть даже не дошли до модели
    (Soro стоит на внешнем хосте, и сеть до него иногда мигает). Вызов
    стоит около полутора секунд, так что второй заход дешевле, чем отчёт
    сводкой вместо текста. Повторяем только транспортные ошибки: 4xx от
    модели повторять бессмысленно, а 5xx — уже её беда, а не сети.
    """
    messages = build_messages(question, facts, bank_name)
    for attempt in (1, 2):
        try:
            raw = await llm.complete(
                messages,
                max_tokens=REPORT_MAX_TOKENS,
                temperature=settings.SORO_TEMPERATURE,
                client=client,
            )
        except httpx.TransportError as exc:
            if attempt == 2:
                raise
            log.warning(
                "модель не ответила (%s), пробую второй раз", type(exc).__name__
            )
            continue
        return collapse_repeats(strip_markdown(raw))
    return ""


def empty_text(question: str, period: Period) -> str:
    """Ответ на период без единого обращения — без модели, см. `EMPTY_TEXT`."""
    language = phrases.detect_language(question) or "ru"
    return EMPTY_TEXT[language].format(title=period.title)


async def build(
    session: AsyncSession,
    question: str,
    *,
    workspace: Workspace,
    channel: str = "console",
    requested_by: str | None = None,
    now: datetime | None = None,
    client=None,
) -> Report:
    """Вопрос словами → готовый отчёт. Единственный вход для всех каналов.

    `channel` и `requested_by` идут в аудит-лог: цифры банка — то, о чём
    через год спросят «кто это выгружал». Вопрос в аудите лежит как есть:
    он написан сотрудником банка про свои же цифры, персональных данных
    клиентов в нём нет (правило 1 `core/audit`).
    """
    started = time.monotonic()
    period = parse_period(question, now)
    data = await collect(session, workspace.id, period)
    facts = format_facts(data, period)

    await audit.record(
        session,
        workspace,
        audit.EVENT_REPORT,
        {
            "channel": channel,
            "requested_by": requested_by,
            "question": question,
            "period": period.title,
            "since": period.since.isoformat(),
            "until": period.until.isoformat(),
            "conversations": data["conversations"]["total"],
        },
    )

    warnings: list[str] = []
    if period.assumed:
        # Оговорка идёт в ответ ОТДЕЛЬНОЙ строкой, а не просьбой к модели:
        # правило «скажи, что период выбран по умолчанию» модель забывает
        # через раз, а цифры за другой период владелец примет за свои.
        warnings.append(
            f"Период в запросе не назван — показываю {period.name}. "
            "Напишите «за прошлый месяц» или «за июнь», если нужен другой."
        )

    degraded = False
    if not data["conversations"]["total"]:
        # Пересказывать нечего, и модель на пустой сводке зацикливается —
        # см. `EMPTY_TEXT`. Это не деградация: ответ правильный и полный.
        text_out = empty_text(question, period)
    else:
        try:
            text_out = await narrate(
                question, facts, bank_name=workspace.name, client=client
            )
        except Exception as exc:  # noqa: BLE001 — цифры важнее пересказа
            log.warning(
                "модель не пересказала отчёт (%s): %s", type(exc).__name__, exc
            )
            text_out = ""

        if not text_out:
            text_out = f"{MODEL_DOWN_NOTE}\n\n{facts}"
            degraded = True

    return Report(
        period=period,
        data=data,
        facts=facts,
        text=text_out,
        latency_ms=int((time.monotonic() - started) * 1000),
        degraded=degraded,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# кто и когда просит отчёт
# ---------------------------------------------------------------------------
#
# В консоли отчёт спрашивают на экране 08 — там вопрос заведомо про цифры.
# В Telegram один и тот же бот отвечает и клиентам банка, и владельцу,
# поэтому нужны два признака: КТО пишет и ПРО ЧТО.

# Слова, по которым узнаём просьбу об отчёте. Русские и таджикские вместе:
# владелец пишет на том языке, на котором ему удобнее.
REPORT_WORDS_RE = re.compile(
    r"отч[её]т|аналитик|статистик|сводк|показател|метрик|дашборд|итог[иа]?\b|"
    r"сколько\s+(обращен|диалог|вопрос|клиент)|"
    r"ҳисобот|хисобот|омор|таҳлил|тахлил",
    re.I,
)


def looks_like_report_request(question: str) -> bool:
    """Просят ли отчёт. Проверяется ТОЛЬКО для владельца (см. `is_owner`).

    Признак намеренно узкий — по словам «отчёт», «аналитика», «статистика».
    Широкий («любой вопрос владельца — это отчёт») сломал бы владельцу
    возможность проверить бота как клиент: он пишет «фоизи амонат чанд
    аст?» и должен получить ответ по документам, а не сводку.
    """
    return bool(REPORT_WORDS_RE.search(question or ""))


def owner_ids() -> frozenset[str]:
    """Кому в Telegram доступны цифры банка — список из `.env`.

    ПОЧЕМУ ВООБЩЕ СПИСОК. Бот в Telegram публичный: его находят по имени и
    пишут ему без приглашения. Без проверки любой клиент, написавший
    «дай статистику», получил бы обороты банка — это не «фича демо», это
    утечка. Список id — самый простой замок, который нельзя обойти
    формулировкой вопроса.

    Пусто — значит отчётов в Telegram нет ни у кого: на новом стенде
    безопаснее молчать, чем отвечать всем.
    """
    raw = settings.OWNER_TELEGRAM_IDS or ""
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def is_owner(external_id: str | None) -> bool:
    return bool(external_id) and str(external_id) in owner_ids()

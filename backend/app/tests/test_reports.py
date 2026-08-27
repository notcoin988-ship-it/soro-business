"""Отчёт словами: разбор периода, цифры за период, доступ в Telegram.

Экран 08 и та же просьба в Telegram держатся на одном обещании: цифры и
период считает КОД, модель только пересказывает. Поэтому здесь три группы
проверок:

1. разбор фразы в границы — без базы и без модели, это чистая функция;
2. цифры за произвольный период — с настоящим PostgreSQL и датами,
   расставленными руками: попадание в границы важнее любой арифметики;
3. кто может получить отчёт в Telegram — бот публичный, и цифры банка
   не должны уехать клиенту, написавшему «дай статистику».
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.core import dialog, escalation, llm, reports
from app.core.reports import DUSHANBE
from app.db import get_session
from app.main import app
from app.tests.fixture_llm import FakeSoro

# Точка отсчёта для всех проверок разбора: понедельник 17 августа 2026,
# 13:40 по Душанбе. Фиксированная дата обязательна — «эта неделя»,
# посчитанная от `now()`, в понедельник и в субботу даёт разные границы, и
# тест на живом времени падал бы по календарю, а не по коду.
NOW = datetime(2026, 8, 17, 13, 40, tzinfo=DUSHANBE)


def local(*args) -> datetime:
    return datetime(*args, tzinfo=DUSHANBE)


# ---------------------------------------------------------------------------
# разбор периода
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question, since, until",
    [
        # «эта неделя» — с понедельника по «сейчас», а не по конец недели:
        # отчёт за будущие дни этой недели не существует.
        ("Нужен отчёт аналитики за эту неделю", local(2026, 8, 17), NOW),
        ("покажи цифры на этой неделе", local(2026, 8, 17), NOW),
        (
            "отчёт за прошлую неделю",
            local(2026, 8, 10),
            local(2026, 8, 17),
        ),
        # прошлый месяц целиком, а не «30 дней назад»
        ("аналитика за прошлый месяц", local(2026, 7, 1), local(2026, 8, 1)),
        ("отчёт за этот месяц", local(2026, 8, 1), NOW),
        ("сегодня сколько обращений?", local(2026, 8, 17), NOW),
        ("а вчера?", local(2026, 8, 16), local(2026, 8, 17)),
        # месяц по имени: год не назван, июнь этого года уже прошёл
        ("дай отчёт за июнь", local(2026, 6, 1), local(2026, 7, 1)),
        ("отчёт по июню 2025", local(2025, 6, 1), local(2025, 7, 1)),
        # декабрь ещё не наступил — значит прошлогодний: отчётов за будущее
        # не бывает
        ("нужен отчёт за декабрь", local(2025, 12, 1), local(2026, 1, 1)),
        # скользящие окна считаются от «сейчас», как на экране 07
        ("отчёт за 14 дней", NOW - timedelta(days=14), NOW),
        ("статистика за две недели", NOW - timedelta(days=14), NOW),
        # таджикский: те же правила
        ("Ҳисобот барои ҳафтаи гузашта", local(2026, 8, 10), local(2026, 8, 17)),
        ("омори имрӯз", local(2026, 8, 17), NOW),
        ("ҳисобот барои моҳи гузашта", local(2026, 7, 1), local(2026, 8, 1)),
    ],
)
def test_period_is_parsed_from_the_phrase(question, since, until):
    period = reports.parse_period(question, NOW)

    assert period.since == since
    assert period.until == until
    assert period.assumed is False


def test_period_without_a_hint_falls_back_to_a_week_and_says_so():
    """«Как у нас дела?» — период не назван.

    Отвечать ошибкой нельзя: руководитель задал осмысленный вопрос. Но и
    молчать о подмене нельзя — цифры за неделю, принятые за цифры за месяц,
    это самая дорогая ошибка этого экрана.
    """
    period = reports.parse_period("как у нас дела?", NOW)

    assert period.assumed is True
    assert period.since == NOW - timedelta(days=reports.DEFAULT_DAYS)


@pytest.mark.parametrize(
    "question",
    [
        "отчёт по маркетингу",  # «мар» внутри слова — это не март
        "аналитика по апрельским заявкам за эту неделю",  # неделя важнее месяца
    ],
)
def test_month_names_are_not_found_inside_other_words(question):
    """Сторож к списку месяцев: стебель должен быть словом, а не обрывком.

    «Маркетинг» с мартом путать нельзя — руководитель получил бы цифры за
    другой период и ничего бы не заметил.
    """
    period = reports.parse_period(question, NOW)

    assert period.since != local(2026, 3, 1)
    assert period.since != local(2026, 4, 1)


def test_period_is_capped():
    """Потолок глубины: у `messages` нет индекса по одному `created_at`."""
    period = reports.parse_period("отчёт за 5000 дней", NOW)

    assert period.days <= reports.MAX_RANGE_DAYS


def test_title_holds_both_the_name_and_the_dates():
    """Название периода без дат — повод для спора «а какая это неделя?»."""
    period = reports.parse_period("отчёт за прошлую неделю", NOW)

    assert period.name == "прошлая неделя"
    assert "10–16 августа 2026" in period.title


# ---------------------------------------------------------------------------
# цифры за период
# ---------------------------------------------------------------------------


async def talk(session, workspace, *, external_id, started_at, escalated=False):
    """Диалог с проставленной вручную датой начала.

    Даты расставляем сами, а не полагаемся на `now()`: весь смысл проверки —
    что в отчёт попадает только то, что лежит внутри границ.
    """
    identity = await dialog.resolve_identity(
        session, workspace.id, "telegram", external_id
    )
    conversation = await dialog.resolve_conversation(
        session, workspace.id, identity.contact_id
    )
    conversation.started_at = started_at
    message = await dialog.save_message(
        session,
        conversation=conversation,
        channel="telegram",
        role="user",
        text="Фоизи амонат чанд аст?",
    )
    message.created_at = started_at
    if escalated:
        record = await escalation.escalate(
            session, conversation, escalation.REASON_NO_ANSWER
        )
        record.created_at = started_at
    await session.flush()
    return conversation


async def test_only_dialogs_inside_the_period_are_counted(session, workspace):
    """Граница периода — это граница, а не пожелание."""
    await talk(session, workspace, external_id="in-1", started_at=local(2026, 7, 5))
    await talk(session, workspace, external_id="in-2", started_at=local(2026, 7, 31, 23))
    # ровно на верхней границе: июль кончается 1 августа 00:00, и это уже
    # август — иначе диалог попал бы в оба месяца
    await talk(session, workspace, external_id="out-1", started_at=local(2026, 8, 1))
    await talk(session, workspace, external_id="out-2", started_at=local(2026, 6, 30, 23))

    july = reports.parse_period("отчёт за июль 2026", NOW)
    data = await reports.collect(session, workspace.id, july)

    assert data["conversations"]["total"] == 2


async def test_numbers_match_the_analytics_screen(client, session, workspace):
    """Отчёт за 7 дней и экран 07 обязаны показать одно и то же.

    Ради этого запросы и переехали в `core/reports`: два места, считающие
    «сколько обращений», однажды разойдутся, и объяснять расхождение придётся
    банку.
    """
    now = datetime.now(tz=timezone.utc)
    await talk(session, workspace, external_id="m-1", started_at=now - timedelta(days=1))
    await talk(
        session,
        workspace,
        external_id="m-2",
        started_at=now - timedelta(days=2),
        escalated=True,
    )

    async with client:
        screen = (await client.get("/api/analytics?days=7")).json()

    week = reports.rolling_period(7, now)
    data = await reports.collect(session, workspace.id, week)

    assert data["conversations"] == screen["conversations"]
    assert data["hours_saved"] == screen["hours_saved"]


async def test_empty_period_is_zeros_not_a_crash(session, workspace):
    data = await reports.collect(
        session, workspace.id, reports.parse_period("отчёт за январь 2020", NOW)
    )

    assert data["conversations"]["total"] == 0
    assert data["median_latency_ms"] is None


# ---------------------------------------------------------------------------
# сводка для модели
# ---------------------------------------------------------------------------


def test_facts_name_the_median_a_median():
    """Первый живой прогон отдавал модели сырой JSON, и `median_latency_ms`
    превратился в «среднее время ответа». Подпись в сводке — это защита от
    такой подмены, и она обязана быть именно такой."""
    data = {
        "conversations": {"total": 3, "by_bot": 2, "by_operator": 1, "bot_share": 67},
        "hours_saved": 0.2,
        "median_latency_ms": 900,
        "channels": [{"channel": "telegram", "conversations": 3}],
        "languages": [{"lang": "tj", "messages": 4}],
        "top_questions": [{"question": "фоизи амонат", "count": 2}],
        "attention": {"no_answer": 1},
        "rating": {"total": 0, "average": None},
    }
    facts = reports.format_facts(data, reports.parse_period("за эту неделю", NOW))

    assert "Медиана времени ответа бота: 0,9 с" in facts
    assert "среднее" not in facts.lower()
    # оценок нет — строки про оценки быть не должно, иначе модель напишет
    # абзац про их отсутствие
    assert "Оценка клиентов" not in facts
    assert "Период: эта неделя" in facts


def test_facts_of_an_empty_period_do_not_invent_rows():
    data = {
        "conversations": {"total": 0, "by_bot": 0, "by_operator": 0, "bot_share": 0},
        "hours_saved": 0.0,
        "median_latency_ms": None,
        "channels": [],
        "languages": [],
        "top_questions": [],
        "attention": {"no_answer": 0},
        "rating": {"total": 0, "average": None},
    }
    facts = reports.format_facts(data, reports.parse_period("за июнь", NOW))

    assert "Обращений всего: 0" in facts
    assert "Сэкономлено" not in facts
    assert "Медиана" not in facts


@pytest.mark.parametrize(
    "raw, clean",
    [
        ("**Отчёт** за июнь", "Отчёт за июнь"),
        ("## Итоги\n- 42 обращения", "Итоги\n- 42 обращения"),
    ],
)
def test_markdown_is_stripped(raw, clean):
    """Правило 6 промпта — просьба, а не гарантия. В Telegram
    `parse_mode=None`, и «**» уехало бы руководителю как есть."""
    assert reports.strip_markdown(raw) == clean


# ---------------------------------------------------------------------------
# ручка экрана 08
# ---------------------------------------------------------------------------


@pytest.fixture
def client(session, workspace, monkeypatch):
    monkeypatch.setattr(settings, "WORKSPACE_DEFAULT_SLUG", workspace.slug)
    app.dependency_overrides[get_session] = lambda: session
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://console")
    app.dependency_overrides.clear()


@pytest.fixture
def fake_model(monkeypatch):
    server = FakeSoro(condensed="За эту неделю обращений было 3, бот закрыл 2.").start()
    monkeypatch.setattr(llm.settings, "SORO_API_URL", server.base_url, raising=False)
    yield server
    server.stop()


async def test_ask_returns_text_period_and_the_facts_behind_it(
    client, session, workspace, fake_model
):
    """Ручка отдаёт не только текст: экран показывает рядом сводку, из
    которой он собран, — это главный аргумент на встрече."""
    await talk(
        session,
        workspace,
        external_id="ask-1",
        started_at=datetime.now(tz=timezone.utc) - timedelta(hours=2),
    )

    async with client:
        response = await client.post(
            "/api/reports/ask", json={"question": "нужен отчёт за эту неделю"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "За эту неделю обращений было 3, бот закрыл 2."
    assert body["period"]["name"] == "эта неделя"
    assert body["period"]["assumed"] is False
    assert "Обращений всего: 1" in body["facts"]
    assert body["data"]["conversations"]["total"] == 1
    assert body["degraded"] is False


async def test_only_the_facts_reach_the_model(client, session, workspace, fake_model):
    """В модель уходит сводка и вопрос — и ничего больше.

    Проверяем прямо по телу запроса: если однажды туда попадёт сырой JSON или
    свободный доступ к базе, обещание «модель не считает» перестанет быть
    правдой незаметно.
    """
    await talk(session, workspace, external_id="jun-1", started_at=local(2026, 6, 15))

    async with client:
        await client.post("/api/reports/ask", json={"question": "отчёт за июнь"})

    sent = fake_model.requests[-1]
    user_message = sent["messages"][-1]["content"]

    assert "<данные>" in user_message
    assert "Период: июнь 2026" in user_message
    assert sent["stream"] is False
    assert "median_latency_ms" not in user_message


async def test_model_down_still_gives_the_numbers(client, session, workspace, monkeypatch):
    """Модель недоступна — цифры уже посчитаны, и отдать их куда лучше, чем
    показать ошибку: сводка без пересказа остаётся отчётом."""
    async def boom(*args, **kwargs):
        raise RuntimeError("модель недоступна")

    monkeypatch.setattr(reports.llm, "complete", boom)
    await talk(
        session,
        workspace,
        external_id="down-1",
        started_at=datetime.now(tz=timezone.utc) - timedelta(hours=1),
    )

    async with client:
        response = await client.post(
            "/api/reports/ask", json={"question": "отчёт за эту неделю"}
        )

    body = response.json()
    assert response.status_code == 200
    assert body["degraded"] is True
    assert "Обращений всего" in body["text"]


async def test_assumed_period_is_reported_as_a_warning(
    client, session, workspace, fake_model
):
    async with client:
        response = await client.post("/api/reports/ask", json={"question": "как дела?"})

    body = response.json()
    assert body["period"]["assumed"] is True
    assert body["warnings"], "оговорка про период обязательна"


async def test_empty_period_does_not_go_to_the_model_at_all(
    client, session, workspace, fake_model
):
    """Пустой период отвечается без модели.

    ЖИВОЙ ПРОГОН, из-за которого это сделано: на июне (обращений ноль)
    модель повторила «Доля ответов… не посчитана, так как обращений не было»
    двадцать три раза, пока не кончились токены. Пересказывать нечего —
    значит и звать некого.
    """
    async with client:
        response = await client.post(
            "/api/reports/ask", json={"question": "отчёт за январь 2020"}
        )

    body = response.json()
    assert fake_model.requests == [], "на пустом периоде модель звать незачем"
    assert "обращений не было" in body["text"]
    # Это правильный полный ответ, а не аварийный: сводкой цифрами его
    # подменять не нужно.
    assert body["degraded"] is False


async def test_a_dropped_connection_is_retried_once(monkeypatch):
    """Обрыв соединения — не повод отдавать сводку вместо отчёта.

    Живой прогон: два вызова из двадцати упали с ConnectError за 30–45 мс,
    то есть даже не дошли до модели. Вызов стоит полторы секунды — второй
    заход дешевле потери текста.
    """
    calls: list[int] = []

    async def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("сеть мигнула")
        return "Отчёт за неделю: 42 обращения."

    monkeypatch.setattr(reports.llm, "complete", flaky)

    text_out = await reports.narrate("отчёт", "Обращений всего: 42", bank_name="Банк")

    assert text_out == "Отчёт за неделю: 42 обращения."
    assert len(calls) == 2


async def test_a_second_failure_is_not_retried_forever(monkeypatch):
    async def always_down(*args, **kwargs):
        raise httpx.ConnectError("сеть лежит")

    monkeypatch.setattr(reports.llm, "complete", always_down)

    with pytest.raises(httpx.ConnectError):
        await reports.narrate("отчёт", "Обращений всего: 42", bank_name="Банк")


def test_looping_answer_is_cut():
    """Сторож против зацикливания: промпт и пример лечат его почти всегда, а
    «почти» уедет руководителю простынёй в двадцать строк."""
    looped = "Отчёт за июнь.\n" + "Не посчитано.\n" * 20

    assert reports.collapse_repeats(looped).count("Не посчитано") == 2


async def test_empty_question_is_rejected(client):
    async with client:
        assert (
            await client.post("/api/reports/ask", json={"question": "   "})
        ).status_code == 422


# ---------------------------------------------------------------------------
# доступ к отчётам в Telegram
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("", frozenset()),
        ("111", frozenset({"111"})),
        (" 111 , 222 ", frozenset({"111", "222"})),
    ],
)
def test_owner_list_is_read_from_env(monkeypatch, raw, expected):
    monkeypatch.setattr(settings, "OWNER_TELEGRAM_IDS", raw, raising=False)

    assert reports.owner_ids() == expected


def test_nobody_is_an_owner_by_default(monkeypatch):
    """Пустой список — это «отчётов в Telegram нет ни у кого».

    Обратное поведение («список пуст, значит всем можно») превратило бы
    новый стенд в утечку с первой минуты.
    """
    monkeypatch.setattr(settings, "OWNER_TELEGRAM_IDS", "", raising=False)

    assert reports.is_owner("111") is False


@pytest.mark.parametrize(
    "text",
    [
        "нужен отчёт аналитики по этой неделе",
        "дай статистику за июнь",
        "покажи аналитику",
        "сколько обращений было вчера?",
        "Ҳисобот барои моҳи гузашта",
        "омори ҳафта",
    ],
)
def test_report_requests_are_recognised(text):
    assert reports.looks_like_report_request(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Фоизи амонати Ояндасоз чанд аст?",
        "Какая ставка по вкладу?",
        "хочу оператора",
    ],
)
def test_client_questions_are_not_report_requests(text):
    """Владелец должен уметь проверить бота как клиент: его вопрос про
    вклад обязан уйти в базу знаний, а не в сводку."""
    assert reports.looks_like_report_request(text) is False

"""Аналитика экрана 07 (`GET /api/analytics`, приложение Б ТЗ).

Критерий сдачи недели 5 звучит так: «после серии действий вручную цифры
на экране совпадают с фактом». Тесты и делают эту серию действий —
заводят диалоги, эскалации и сообщения — и сверяют каждую цифру с тем,
что было заведено.

Воркспейс подменяется на тестовый: эндпоинт всегда смотрит в воркспейс из
`.env`, то есть в живую базу разработки, где числа меняются сами по себе.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.core import dialog, escalation
from app.db import get_session
from app.main import app

CARD = "5058123456789012"


@pytest.fixture
def client(session, workspace, monkeypatch):
    monkeypatch.setattr(settings, "WORKSPACE_DEFAULT_SLUG", workspace.slug)
    app.dependency_overrides[get_session] = lambda: session
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://console")
    app.dependency_overrides.clear()


async def conversation_with(
    session, workspace, *, channel="telegram", external_id="an-1"
):
    identity = await dialog.resolve_identity(
        session, workspace.id, channel, external_id
    )
    return await dialog.resolve_conversation(
        session, workspace.id, identity.contact_id
    )


async def say(session, conversation, text, *, channel="telegram", role="user", ms=None):
    return await dialog.save_message(
        session,
        conversation=conversation,
        channel=channel,
        role=role,
        text=text,
        latency_ms=ms,
    )


async def get(client, days=7) -> dict:
    async with client:
        response = await client.get(f"/api/analytics?days={days}")
    assert response.status_code == 200
    return response.json()


# ---------------------------------------------------------------------------
# исходы диалогов
# ---------------------------------------------------------------------------


async def test_empty_period_is_zeros_not_a_crash(client):
    body = await get(client)

    assert body["conversations"] == {
        "total": 0,
        "by_bot": 0,
        "by_operator": 0,
        "bot_share": 0,
    }
    assert body["hours_saved"] == 0
    assert body["median_latency_ms"] is None


async def test_escalated_dialog_counts_as_operator(client, session, workspace):
    """Диалог, дошедший до человека, из «снято с колл-центра» вычитается.

    Ровно эту цифру банк и покупает, и завысить её значит соврать в самом
    важном месте экрана.
    """
    quiet = await conversation_with(session, workspace, external_id="an-quiet")
    await say(session, quiet, "Фоизи амонат чанд аст?")

    loud = await conversation_with(session, workspace, external_id="an-loud")
    await say(session, loud, "Хочу оператора")
    await escalation.escalate(session, loud, escalation.REASON_USER_REQUEST)

    body = await get(client)

    assert body["conversations"]["total"] == 2
    assert body["conversations"]["by_bot"] == 1
    assert body["conversations"]["by_operator"] == 1
    assert body["conversations"]["bot_share"] == 50


async def test_hours_saved_are_counted_at_four_and_a_half_minutes(
    client, session, workspace
):
    """Экономия времени — это цифра прототипа (4,5 мин на разговор),
    умноженная на диалоги, которые бот закрыл сам."""
    for number in range(4):
        conversation = await conversation_with(
            session, workspace, external_id=f"an-{number}"
        )
        await say(session, conversation, "Салом")

    body = await get(client)

    assert body["conversations"]["by_bot"] == 4
    assert body["hours_saved"] == 0.3  # 4 * 4,5 мин = 18 мин


# ---------------------------------------------------------------------------
# скорость
# ---------------------------------------------------------------------------


async def test_median_ignores_a_single_slow_answer(client, session, workspace):
    """Медиана, а не среднее: один медленный ответ не должен решать,
    уложились ли мы в норматив «< 6 сек»."""
    conversation = await conversation_with(session, workspace)
    for ms in (900, 1000, 1100, 30000):
        await say(session, conversation, "ответ", role="assistant", ms=ms)

    body = await get(client)

    assert body["median_latency_ms"] == 1050


# ---------------------------------------------------------------------------
# каналы, языки, темы
# ---------------------------------------------------------------------------


async def test_channel_is_taken_from_the_first_message(client, session, workspace):
    """Склеенный диалог считается ОДИН раз — по каналу, с которого начали.

    Иначе после склейки контактов один разговор попадал бы в статистику
    и как Telegram, и как виджет, и сумма по каналам разошлась бы с
    числом диалогов.
    """
    conversation = await conversation_with(session, workspace)
    await say(session, conversation, "Салом", channel="telegram")
    await say(session, conversation, "Продолжу с компьютера", channel="widget")

    body = await get(client)

    assert body["channels"] == [{"channel": "telegram", "conversations": 1}]


async def test_languages_split_by_letters_and_words(client, session, workspace):
    """Таджикский узнаётся и без диакритики.

    Живой прогон показал, зачем это нужно: эвристика ТЗ смотрит только на
    буквы ӣӯҳҷғқ, а клиенты пишут с телефонной раскладки без них — экран
    показывал «100% русского» на половине таджикских вопросов.
    """
    conversation = await conversation_with(session, workspace)
    await say(session, conversation, "Мӯҳлаташ чанд сол?")  # буквы
    await say(session, conversation, "Фоизи амонати Ояндасоз чанд аст?")  # слова
    await say(session, conversation, "салом мехохам кредить гирам")  # слова
    await say(session, conversation, "Какая ставка по вкладу?")
    await say(session, conversation, "hello there")

    body = await get(client)
    languages = {row["lang"]: row["messages"] for row in body["languages"]}

    assert languages["tj"] == 3
    assert languages["ru"] == 1
    assert languages["other"] == 1


@pytest.mark.parametrize(
    "text",
    [
        "Сколько стоит пластиковая карта?",  # «пласт» внутри слова
        "Хочу принять участие в акции",  # «аст» внутри «участие»
        "Подарок ко дню рождения",
    ],
)
async def test_russian_is_not_mistaken_for_tajik(client, session, workspace, text):
    """Сторож к списку слов: границы слова обязательны, иначе «аст»
    находится внутри «участие», и русские вопросы уезжают в таджикские."""
    conversation = await conversation_with(session, workspace)
    await say(session, conversation, text)

    body = await get(client)
    languages = {row["lang"]: row["messages"] for row in body["languages"]}

    assert languages.get("tj", 0) == 0
    assert languages["ru"] == 1


async def test_top_questions_come_from_masked_text(client, session, workspace):
    """В темах — маскированный текст. Экран 07 показывают правлению, и
    номер карты клиента там появиться не может."""
    conversation = await conversation_with(session, workspace)
    await say(session, conversation, f"Корти ман {CARD} кор намекунад")

    body = await get(client)
    questions = " ".join(row["question"] for row in body["top_questions"])

    assert CARD not in questions
    assert "[CARD]" in questions


async def test_top_questions_are_grouped_and_sorted(client, session, workspace):
    conversation = await conversation_with(session, workspace)
    for _ in range(3):
        await say(session, conversation, "Фоизи амонат чанд аст?")
    await say(session, conversation, "Кай кушода мешавад?")

    body = await get(client)

    assert body["top_questions"][0] == {
        "question": "Фоизи амонат чанд аст?",
        "count": 3,
    }


# ---------------------------------------------------------------------------
# требует внимания
# ---------------------------------------------------------------------------


async def test_no_answer_escalations_are_counted_separately(
    client, session, workspace
):
    """«Бот не нашёл ответа» — это задача для базы знаний, а «позовите
    человека» — нет. На экране они не должны смешиваться."""
    lost = await conversation_with(session, workspace, external_id="an-lost")
    await say(session, lost, "Комиссия за перевод в Дубай?")
    await escalation.escalate(session, lost, escalation.REASON_NO_ANSWER)

    asked = await conversation_with(session, workspace, external_id="an-asked")
    await say(session, asked, "оператор")
    await escalation.escalate(session, asked, escalation.REASON_USER_REQUEST)

    body = await get(client)

    assert body["attention"]["no_answer"] == 1


async def test_days_are_validated(client):
    """`days` приходит из адресной строки. Год назад никто не смотрит, а
    запрос без потолка кладёт базу."""
    async with client:
        assert (await client.get("/api/analytics?days=0")).status_code == 422
        assert (await client.get("/api/analytics?days=365")).status_code == 422

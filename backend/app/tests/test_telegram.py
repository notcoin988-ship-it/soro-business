"""Тесты Telegram-канала (раздел 7.1 ТЗ).

Канал — транспорт, поэтому проверяется ровно транспортное: что пришло от
Telegram, во что превратилось для ядра и что ушло обратно. Ядро при этом
подменяется заглушкой: его логика проверена в test_dialog.py, а тянуть
сюда базу значит проверять одно и то же дважды и медленнее.

Сеть не нужна: апдейт собирается вручную, ответ бота не отправляется.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from aiogram.types import Message
from httpx import ASGITransport, AsyncClient

from app.channels import telegram
from app.config import settings
from app.core.dialog import Reply
from app.main import app

TG_USER = 4242
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def make_message(text: str | None, *, first_name="Далер", last_name="Раҳимов") -> Message:
    """Апдейт в том виде, в каком его присылает Telegram."""
    payload = {
        "message_id": 1,
        "date": int(datetime.now(tz=timezone.utc).timestamp()),
        "chat": {"id": TG_USER, "type": "private"},
        "from": {
            "id": TG_USER,
            "is_bot": False,
            "first_name": first_name,
            "last_name": last_name,
        },
    }
    if text is not None:
        payload["text"] = text
    return Message.model_validate(payload)


@pytest.fixture
def core(monkeypatch):
    """Заглушка ядра: запоминает, с чем его позвали."""
    calls: list[dict] = []

    async def fake_handle(session, **kwargs):
        calls.append(kwargs)
        if kwargs["text"] == "молчи":
            return None
        return Reply(
            text=f"эхо: {kwargs['text']}",
            conversation_id=1,
            message_id=2,
            latency_ms=7,
        )

    monkeypatch.setattr(telegram, "handle_incoming", fake_handle)
    return calls


# ---------------------------------------------------------------------------
# что канал передаёт ядру
# ---------------------------------------------------------------------------


async def test_question_goes_to_core(core):
    answer = await telegram.answer_for(make_message("Фоизи амонат чанд аст?"))

    assert answer == "эхо: Фоизи амонат чанд аст?"
    assert core[0]["channel"] == "telegram"
    assert core[0]["text"] == "Фоизи амонат чанд аст?"


async def test_external_id_is_telegram_user_id(core):
    """`external_id` — id пользователя, а не чата.

    Для личной переписки они совпадают, но в группе chat.id общий, и по
    нему все участники слились бы в один контакт.
    """
    await telegram.answer_for(make_message("Салом"))
    assert core[0]["external_id"] == str(TG_USER)


async def test_display_name_from_profile(core):
    await telegram.answer_for(make_message("Салом"))
    assert core[0]["display_name"] == "Далер Раҳимов"


async def test_bot_silent_when_core_returns_none(core):
    """Ядро вернуло None — диалог у оператора, бот не пишет ничего."""
    assert await telegram.answer_for(make_message("молчи")) is None


# ---------------------------------------------------------------------------
# команды и нетекстовые сообщения
# ---------------------------------------------------------------------------


async def test_start_answers_greeting_without_core(core):
    answer = await telegram.answer_for(make_message("/start"))

    assert answer == telegram.GREETING
    assert core == [], "/start не должен создавать диалог в базе"


async def test_start_with_link_token_still_greets(core):
    """`/start <token>` — переход из виджета. Склейка контактов появится
    вместе с channels/widget.py, но приветствие должно работать уже сейчас."""
    assert await telegram.answer_for(make_message("/start abc123")) == telegram.GREETING


async def test_non_text_message_is_answered(core):
    """Фото или голосовое: клиент должен понять, что его услышали.

    Молчать нельзя — человек решит, что бот сломался, и будет ждать.
    """
    answer = await telegram.answer_for(make_message(None))

    assert answer == telegram.NON_TEXT_REPLY
    assert core == []


# ---------------------------------------------------------------------------
# отчёты руководителю
# ---------------------------------------------------------------------------
#
# Один бот отвечает и клиентам банка, и руководству, поэтому проверяется
# именно развилка: кто спросил и про что. Сам отчёт собирает `core.reports`
# и он проверен в test_reports.py — здесь он подменён заглушкой.


@pytest.fixture
def report(monkeypatch):
    """Заглушка отчёта: запоминает, о чём спросили."""
    asked: list[tuple[str, str]] = []

    async def fake_report(question, external_id):
        asked.append((question, external_id))
        return "отчёт: 42 обращения"

    monkeypatch.setattr(telegram, "report_for", fake_report)
    return asked


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(settings, "OWNER_TELEGRAM_IDS", str(TG_USER), raising=False)


async def test_owner_asking_for_a_report_gets_a_report(core, report, owner):
    answer = await telegram.answer_for(make_message("нужен отчёт за эту неделю"))

    assert answer == "отчёт: 42 обращения"
    assert report[0] == ("нужен отчёт за эту неделю", str(TG_USER))
    assert core == [], "просьба об отчёте не должна заводить диалог клиента"


async def test_owner_can_still_test_the_bot_as_a_client(core, report, owner):
    """Руководитель проверяет бота на своём же вопросе про вклад — и должен
    получить ответ по документам, а не сводку."""
    answer = await telegram.answer_for(make_message("Фоизи амонат чанд аст?"))

    assert answer == "эхо: Фоизи амонат чанд аст?"
    assert report == []


async def test_client_asking_for_statistics_gets_no_numbers(core, report, monkeypatch):
    """ГЛАВНАЯ ПРОВЕРКА ЭТОГО БЛОКА. Бот публичный: его находят по имени и
    пишут без приглашения. Клиент, написавший «дай статистику», обязан
    попасть в обычный поиск по документам, а не получить обороты банка."""
    monkeypatch.setattr(settings, "OWNER_TELEGRAM_IDS", "999999", raising=False)

    answer = await telegram.answer_for(make_message("дай статистику за июнь"))

    assert report == []
    assert answer == "эхо: дай статистику за июнь"


async def test_report_command_from_a_stranger_is_refused_with_his_id(
    core, report, monkeypatch
):
    """Отказ показывает его же id: иначе администратору стенда нечего
    вписать в OWNER_TELEGRAM_IDS. Цифр в отказе нет."""
    monkeypatch.setattr(settings, "OWNER_TELEGRAM_IDS", "", raising=False)

    answer = await telegram.answer_for(make_message("/report"))

    assert str(TG_USER) in answer
    assert report == []
    assert core == [], "команда не должна уходить в поиск по базе знаний"


async def test_report_command_without_a_period_asks_for_the_week(core, report, owner):
    answer = await telegram.answer_for(make_message("/report"))

    assert answer == "отчёт: 42 обращения"
    assert report[0][0] == telegram.REPORT_DEFAULT_ASK


async def test_report_command_carries_the_period(core, report, owner):
    """`/report за июнь` — команда для тех, кто не помнит формулировок."""
    await telegram.answer_for(make_message("/report за июнь"))

    assert report[0][0] == "за июнь"


# ---------------------------------------------------------------------------
# вебхук
# ---------------------------------------------------------------------------


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


UPDATE = {
    "update_id": 1,
    "message": {
        "message_id": 1,
        "date": 1754400000,
        "chat": {"id": TG_USER, "type": "private"},
        "from": {"id": TG_USER, "is_bot": False, "first_name": "Далер"},
        "text": "Салом",
    },
}


async def test_webhook_rejects_wrong_secret(client, monkeypatch):
    """Без проверки секрета эндпоинт открыт всему интернету: кто угодно
    пришлёт «сообщение от клиента» и получит ответ бота."""
    # секрет обязан быть ASCII: Telegram разрешает только [A-Za-z0-9_-],
    # а заголовки HTTP кириллицу не переносят в принципе
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "secret-abc-123")

    response = await client.post(
        telegram.WEBHOOK_PATH, json=UPDATE, headers={SECRET_HEADER: "wrong-secret"}
    )
    assert response.status_code == 403


async def test_webhook_rejects_missing_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "secret-abc-123")

    response = await client.post(telegram.WEBHOOK_PATH, json=UPDATE)
    assert response.status_code == 403


async def test_webhook_accepts_correct_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "secret-abc-123")

    seen = []

    class FakeDispatcher:
        async def feed_update(self, bot, update):
            seen.append(update.update_id)

    monkeypatch.setattr(telegram, "get_dispatcher", lambda: FakeDispatcher())

    response = await client.post(
        telegram.WEBHOOK_PATH, json=UPDATE, headers={SECRET_HEADER: "secret-abc-123"}
    )
    assert response.status_code == 200
    assert seen == [1]


async def test_webhook_answers_200_even_if_handler_fails(client, monkeypatch):
    """Ошибка обработчика не должна возвращаться Telegram.

    На любой не-200 Telegram шлёт апдейт снова — клиент получит один и тот
    же ответ несколько раз, а в базе будут дубли сообщений.
    """
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")

    class BrokenDispatcher:
        async def feed_update(self, bot, update):
            raise RuntimeError("что-то упало внутри")

    monkeypatch.setattr(telegram, "get_dispatcher", lambda: BrokenDispatcher())

    response = await client.post(telegram.WEBHOOK_PATH, json=UPDATE)
    assert response.status_code == 200


async def test_set_webhook_requires_https(monkeypatch):
    """ngrok ещё не запущен — ошибка должна быть понятной, а не «Bad Request»
    от Telegram."""
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "http://localhost:8000")

    with pytest.raises(RuntimeError, match="https"):
        await telegram.set_webhook()

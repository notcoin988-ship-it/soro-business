"""Пределы публичных эндпоинтов.

Виджет открыт всему интернету: его адрес лежит в исходниках сайта банка.
Проверяется то, что защищает стенд от скрипта в цикле, — и то, что защита
не мешает живому человеку.

Redis подделан: считать до двадцати одинаково хорошо и на настоящем, а
подделка позволяет проверить поведение при упавшем Redis, чего на живом
не сделать.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.core import limits
from app.db import get_session
from app.main import app


class FakeRedis:
    """INCR и EXPIRE на словаре. Срок жизни не тикает — тесты укладываются
    в одно окно, а истечение проверяется отдельным сбросом."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expires: list[tuple[str, int]] = []

    def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def expire(self, key: str, seconds: int) -> None:
        self.expires.append((key, seconds))


@pytest.fixture
def redis(monkeypatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(limits, "_redis", lambda: fake)
    return fake


@pytest.fixture
def client(session, workspace, monkeypatch):
    monkeypatch.setattr(settings, "WORKSPACE_DEFAULT_SLUG", workspace.slug)
    app.dependency_overrides[get_session] = lambda: session
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://widget")
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# счётчик
# ---------------------------------------------------------------------------


async def test_message_flood_is_stopped(client, redis, monkeypatch):
    """Двадцать первое сообщение за минуту — 429, а не поход в модель."""
    monkeypatch.setattr(settings, "WIDGET_RATE_PER_MIN", 3)

    async with client:
        codes = [
            (
                await client.post(
                    "/widget/messages", json={"uid": "flood", "text": "вопрос"}
                )
            ).status_code
            for _ in range(5)
        ]

    assert codes[:3] == [202, 202, 202]
    assert codes[3:] == [429, 429]


async def test_limit_is_per_client(client, redis, monkeypatch):
    """Лимит считается по клиенту, а не на всех сразу: за одним офисным
    NAT сидит целый банк, и общий счётчик выключил бы их всех."""
    monkeypatch.setattr(settings, "WIDGET_RATE_PER_MIN", 1)

    async with client:
        first = await client.post(
            "/widget/messages", json={"uid": "one", "text": "вопрос"}
        )
        second = await client.post(
            "/widget/messages", json={"uid": "two", "text": "вопрос"}
        )

    assert first.status_code == 202
    assert second.status_code == 202


async def test_window_is_set_once(redis, monkeypatch):
    """Срок ставится на первом обращении. Если продлевать его каждым
    запросом, окно никогда не закончится и лимит станет вечным."""
    monkeypatch.setattr(settings, "WIDGET_RATE_PER_MIN", 10)

    for _ in range(4):
        limits.hit("widget:same")

    assert redis.expires == [("rate:widget:same", limits.WINDOW_SEC)]


async def test_broken_redis_lets_the_client_through(client, monkeypatch):
    """Ограничитель не должен быть единой точкой отказа: клиент банка,
    которому не ответили из-за нашего кеша, хуже лишнего вопроса."""

    def explode():
        raise ConnectionError("redis лёг")

    monkeypatch.setattr(limits, "_redis", explode)

    async with client:
        response = await client.post(
            "/widget/messages", json={"uid": "no-redis", "text": "вопрос"}
        )

    assert response.status_code == 202


async def test_zero_disables_the_limit(redis, monkeypatch):
    monkeypatch.setattr(settings, "WIDGET_RATE_PER_MIN", 0)
    for _ in range(50):
        limits.hit("widget:unlimited")
    assert redis.counts == {}


# ---------------------------------------------------------------------------
# длина вопроса
# ---------------------------------------------------------------------------


def test_long_question_is_trimmed_not_rejected(monkeypatch):
    """Обрезаем, а не отказываем: человек, вставивший три страницы
    договора, хотел спросить по делу — отказ он прочтёт как поломку."""
    monkeypatch.setattr(settings, "MESSAGE_MAX_CHARS", 50)

    trimmed = limits.check_length("а" * 500)

    assert len(trimmed) == 50


def test_normal_question_is_untouched(monkeypatch):
    monkeypatch.setattr(settings, "MESSAGE_MAX_CHARS", 1000)
    text = "Фоизи амонат чанд аст?"
    assert limits.check_length(text) == text

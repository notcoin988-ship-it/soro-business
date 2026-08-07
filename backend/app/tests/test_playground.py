"""Площадка: POST + SSE (экран 03, приложение А ТЗ).

Проверяется контракт потока: порядок событий, состав `retrieval` и
телеметрия в `final`. На этом контракте держится правая панель —
«стеклянный ящик», который на демо показывают ИТ-службе банка.

Модель подставная (`fixture_llm.FakeSoro`), база знаний наполнена
настоящими векторами.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func

from app.api import playground
from app.core import llm
from app.db import get_session
from app.main import app
from app.models import Chunk, Document
from app.tests.fixture_llm import FakeSoro

pytestmark = pytest.mark.usefixtures("demo_workspace")

FRAGMENTS = [
    (1, "Амонати «Ояндасоз»: фоизи солона 14,5%, ҳадди ақал 500 сомонӣ."),
    (2, "Ҷуброни пеш аз мӯҳлат аз рӯи фоизи «дархостӣ» — 0,5%."),
]


@pytest.fixture
async def client(session):
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def knowledge(session, demo_workspace):
    """Фрагменты кладём в демо-воркспейс: площадка работает именно с ним."""
    from app.ingest.worker import embed

    document = Document(
        workspace_id=demo_workspace.id,
        kind="pdf",
        title="Тарифҳои амонатҳо",
        status="ready",
    )
    session.add(document)
    await session.flush()

    vectors = await embed([text for _, text in FRAGMENTS])
    for (page, text), vector in zip(FRAGMENTS, vectors):
        session.add(
            Chunk(
                workspace_id=demo_workspace.id,
                document_id=document.id,
                page=page,
                ord=page,
                text=text,
                embedding=vector,
            )
        )
    await session.flush()
    await session.execute(
        Chunk.__table__.update()
        .where(Chunk.document_id == document.id)
        .values(tsv=func.to_tsvector("simple", func.lower(Chunk.text)))
    )
    await session.flush()
    return document


@pytest.fixture(autouse=True)
def reachable_threshold(monkeypatch):
    """Порог опущен: проверяется контракт потока, а не значение порога.

    Причина та же, что в test_dialog_rag.py: тест не должен падать
    оттого, что кто-то откалибровал `RAG_MIN_SCORE`.
    """
    monkeypatch.setattr(playground.settings, "RAG_MIN_SCORE", 0.3, raising=False)


@pytest.fixture
def soro(monkeypatch):
    server = FakeSoro(reply="Фоизи солона 14,5% мебошад [1].").start()
    monkeypatch.setattr(llm.settings, "SORO_API_URL", server.base_url, raising=False)
    yield server
    server.stop()


async def read_events(client, message_id: str) -> list[tuple[str, dict]]:
    """Разобрать SSE-поток в список (событие, данные)."""
    events: list[tuple[str, dict]] = []
    async with client.stream(
        "GET", "/api/playground/stream", params={"message_id": message_id}
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        name = None
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:") and name:
                events.append((name, json.loads(line[5:].strip())))
                name = None
    return events


async def ask(client, text: str) -> list[tuple[str, dict]]:
    posted = await client.post("/api/playground/messages", json={"text": text})
    assert posted.status_code == 202
    return await read_events(client, posted.json()["message_id"])


# ---------------------------------------------------------------------------
# POST
# ---------------------------------------------------------------------------


async def test_post_returns_message_id(client):
    response = await client.post(
        "/api/playground/messages", json={"text": "Фоиз чанд аст?"}
    )
    assert response.status_code == 202
    assert response.json()["message_id"]


async def test_empty_question_rejected(client):
    response = await client.post("/api/playground/messages", json={"text": "   "})
    assert response.status_code == 422


async def test_unknown_message_id_is_404(client):
    response = await client.get(
        "/api/playground/stream", params={"message_id": "нет-такого"}
    )
    assert response.status_code == 404


async def test_message_id_is_single_use(client, knowledge, soro):
    """Второй раз тот же id не работает: иначе перезагрузка вкладки
    повторяла бы вопрос и жгла вызовы модели."""
    posted = await client.post(
        "/api/playground/messages", json={"text": "Фоиз чанд аст?"}
    )
    message_id = posted.json()["message_id"]

    await read_events(client, message_id)
    again = await client.get(
        "/api/playground/stream", params={"message_id": message_id}
    )
    assert again.status_code == 404


# ---------------------------------------------------------------------------
# порядок и состав событий
# ---------------------------------------------------------------------------


async def test_retrieval_comes_before_generation(client, knowledge, soro):
    """Требование приложения А: фрагменты приходят ДО начала генерации.

    Иначе правая панель заполняется после ответа, и весь смысл
    «стеклянного ящика» теряется — зритель уже прочитал ответ.
    """
    events = [name for name, _ in await ask(client, "Фоизи амонат чанд аст?")]

    assert events[0] == "retrieval"
    assert "delta" in events
    assert events.index("retrieval") < events.index("delta")
    assert events[-1] == "final"


async def test_retrieval_carries_fragments_with_scores(client, knowledge, soro):
    """Правая панель рисует источник, близость и текст фрагмента."""
    events = dict(await ask(client, "Фоизи амонати Ояндасоз чанд аст?"))
    retrieval = events["retrieval"]

    assert retrieval["fragments"], "фрагменты не пришли"
    first = retrieval["fragments"][0]
    assert first["n"] == 1
    assert 0.0 <= first["score"] <= 1.0
    assert first["title"] == "Тарифҳои амонатҳо"
    assert first["text"]
    assert retrieval["min_score"] == playground.settings.RAG_MIN_SCORE


async def test_deltas_assemble_into_final_text(client, knowledge, soro):
    """Куски складываются в тот же текст, что пришёл в final."""
    events = await ask(client, "Фоизи амонат чанд аст?")
    deltas = "".join(data["text"] for name, data in events if name == "delta")
    final = [data for name, data in events if name == "final"][0]

    assert deltas == soro.reply
    assert final["text"] == soro.reply


async def test_final_carries_telemetry(client, knowledge, soro):
    """Телеметрия — из события final (требование приложения А).

    Четыре числа под панелью: поиск, генерация, всего, токенов.
    """
    final = dict(await ask(client, "Фоизи амонат чанд аст?"))["final"]
    tel = final["telemetry"]

    assert set(tel) == {"search_ms", "generation_ms", "total_ms", "tokens"}
    assert tel["search_ms"] > 0
    assert tel["tokens"] > 0
    assert tel["total_ms"] >= tel["search_ms"] + tel["generation_ms"] - 5


async def test_final_carries_chunks_used(client, knowledge, soro):
    """Ссылка [1] в ответе → id фрагмента, по нему панель подсветит источник."""
    final = dict(await ask(client, "Фоизи амонат чанд аст?"))["final"]
    assert final["chunks_used"]


# ---------------------------------------------------------------------------
# особые случаи
# ---------------------------------------------------------------------------


async def test_below_threshold_skips_model(client, knowledge, soro):
    """Ни один фрагмент не прошёл порог — модель не зовём вовсе.

    Экран показывает это отдельным пустым состоянием, как в эталоне.
    """
    playground.settings.RAG_MIN_SCORE = 0.99
    try:
        events = await ask(client, "Работаете ли вы с криптовалютой?")
    finally:
        playground.settings.RAG_MIN_SCORE = 0.3

    names = [name for name, _ in events]
    assert "delta" not in names
    final = dict(events)["final"]
    assert final["escalated"]
    assert final["reason"] == llm.REASON_NO_ANSWER
    assert soro.requests == [], "модель звали зря"


async def test_greeting_answers_without_search_or_model(client, knowledge, soro):
    """«Салом» — вежливость, а не вопрос: ни поиска, ни модели, ни эскалации.

    На площадке это должно работать так же, как в каналах, иначе демо
    противоречит само себе: в Telegram бот здоровается, а на экране 03
    зовёт оператора.
    """
    events = dict(await ask(client, "салом"))

    assert events["retrieval"]["fragments"] == []
    assert not events["final"]["escalated"]
    assert "Soro" in events["final"]["text"]
    assert soro.requests == [], "модель звали на приветствие"


async def test_unavailable_model_sends_error_event(client, knowledge, soro):
    """Модель лежит — на площадке это видно как есть.

    Здесь, в отличие от каналов, вежливая формулировка не нужна: экран
    показывают ИТ-службе, и ей важна причина.
    """
    soro.stop()
    events = dict(await ask(client, "Фоизи амонат чанд аст?"))

    assert "error" in events
    assert events["error"]["message"] == "Модель недоступна"
    assert events["error"]["detail"]


async def test_question_is_masked_before_search(client, knowledge, soro):
    """В модель и в поиск уходит маска — на площадке это тоже должно
    выполняться, иначе экран врёт про безопасность."""
    card = "5058123456789012"
    events = dict(await ask(client, f"Корти ман {card}, фоизи амонат чанд аст?"))

    assert card not in events["retrieval"]["question"]
    assert "[CARD]" in events["retrieval"]["question"]
    assert card not in json.dumps(soro.requests, ensure_ascii=False)

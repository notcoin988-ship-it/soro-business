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


async def ask(
    client, text: str, thread_id: str | None = None
) -> list[tuple[str, dict]]:
    payload: dict = {"text": text}
    if thread_id:
        payload["thread_id"] = thread_id
    posted = await client.post("/api/playground/messages", json=payload)
    assert posted.status_code == 202
    return await read_events(client, posted.json()["message_id"])


@pytest.fixture(autouse=True)
def clean_threads():
    """История площадки живёт в памяти модуля — чистим между тестами.

    Иначе один тест видел бы ветки другого, и порядок запуска начал бы
    влиять на результат.
    """
    playground._threads.clear()
    yield
    playground._threads.clear()


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


# ---------------------------------------------------------------------------
# память диалога (thread_id)
# ---------------------------------------------------------------------------


async def test_history_reaches_the_model(client, knowledge, soro):
    """Второй вопрос уходит в модель вместе с первым обменом.

    Без этого бот не понимает ответ на собственный уточняющий вопрос —
    та самая поломка, с которой началась память диалога.
    """
    await ask(client, "Фоизи амонат чанд аст?", thread_id="t1")
    await ask(client, "Ҷуброн чӣ хел ҳисоб мешавад?", thread_id="t1")

    roles = [m["role"] for m in soro.requests[-1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert "Фоизи амонат" in soro.requests[-1]["messages"][1]["content"]


async def test_thread_id_is_optional(client, knowledge, soro):
    """Без `thread_id` память не копится: curl и старые клиенты должны
    работать по-прежнему."""
    await ask(client, "Фоизи амонат чанд аст?")
    await ask(client, "Ҷуброн чӣ хел ҳисоб мешавад?")

    assert [m["role"] for m in soro.requests[-1]["messages"]] == ["system", "user"]


async def test_threads_are_isolated(client, knowledge, soro):
    """Две вкладки — два разговора. Смешать их значит показать одному
    сотруднику вопросы другого."""
    await ask(client, "Фоизи амонат чанд аст?", thread_id="t1")
    await ask(client, "Ҷуброн чӣ хел?", thread_id="t2")

    assert [m["role"] for m in soro.requests[-1]["messages"]] == ["system", "user"]


async def test_greeting_is_remembered_too(client, knowledge, soro):
    """«Салом» отвечается без модели, но в истории остаться обязан:
    иначе следующий вопрос увидит разговор, начатый с середины."""
    await ask(client, "салом", thread_id="t1")
    await ask(client, "Фоизи амонат чанд аст?", thread_id="t1")

    messages = soro.requests[-1]["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[1]["content"] == "салом"


async def test_follow_up_is_rewritten_and_found(client, knowledge, monkeypatch):
    """Главный сценарий: реплика, непонятная сама по себе, находится
    после переписывания по истории.

    Первый поиск проваливает порог, второй — по самостоятельной
    формулировке — находит, и клиент получает ответ вместо оператора.
    """
    # Порог поднят, чтобы две попытки заведомо разошлись. Замер на этих
    # фикстурах: «а если раньше?» даёт 0,418, переписанная формулировка —
    # 0,561. Ставим между ними. Больше 0,57 брать нельзя: у bge-m3
    # таджикская шкала занижена, и даже дословная цитата из фрагмента
    # выше 0,56 не поднимается (см. ARCHITECTURE.md).
    monkeypatch.setattr(playground.settings, "RAG_MIN_SCORE", 0.5, raising=False)
    # Бот в первом ответе САМ называет тему, и переписанный вопрос
    # состоит из его же слов. Иначе сработает заслон `_looks_rewritten`:
    # формулировка из ниоткуда — признак того, что модель не переписала
    # реплику, а сочинила ответ.
    server = FakeSoro(
        reply="Ҷуброни пеш аз мӯҳлат аз рӯи фоизи дархостӣ ҳисоб мешавад [1].",
        condensed="Ҷуброни пеш аз мӯҳлат аз рӯи фоизи дархостӣ",
    ).start()
    monkeypatch.setattr(llm.settings, "SORO_API_URL", server.base_url, raising=False)
    try:
        await ask(client, "Фоизи амонати «Ояндасоз» чанд аст?", thread_id="t1")
        events = dict(await ask(client, "а если раньше?", thread_id="t1"))
    finally:
        server.stop()

    retrieval = events["retrieval"]
    assert retrieval["rewritten"], "вопрос не переписали"
    assert retrieval["searched"] == "Ҷуброни пеш аз мӯҳлат аз рӯи фоизи дархостӣ"
    assert retrieval["has_answer"]
    # на экране остаётся то, что человек написал: панель не должна
    # подменять его вопрос нашей формулировкой
    assert retrieval["question"] == "а если раньше?"
    assert not events["final"]["escalated"]


async def test_plain_question_is_not_rewritten(client, knowledge, soro):
    """Вопрос, который нашёлся сразу, переписывать не нужно — ни лишнего
    вызова модели, ни лишних секунд."""
    await ask(client, "Фоизи амонат чанд аст?", thread_id="t1")
    before = len(soro.requests)
    events = dict(await ask(client, "Фоизи амонат чанд аст?", thread_id="t1"))

    assert not events["retrieval"]["rewritten"]
    assert events["retrieval"]["searched"] == "Фоизи амонат чанд аст?"
    # ровно один новый запрос — генерация ответа, без переписывания
    assert len(soro.requests) - before == 1


async def test_thread_cap_drops_oldest(client, knowledge, soro):
    """Вкладку открывают и забывают — без потолка словарь растёт до
    перезапуска бэкенда."""
    for number in range(playground.MAX_THREADS + 5):
        await ask(client, "салом", thread_id=f"t{number}")

    assert len(playground._threads) <= playground.MAX_THREADS
    assert "t0" not in playground._threads, "самая старая ветка осталась"


async def test_repeated_answer_hands_over_to_operator(client, knowledge, monkeypatch):
    """Модель пересказала себя — зовём человека вместо третьего повтора.

    Живая поломка: бот задавал уточняющий вопрос (правило 7 промпта), на
    «бале» приходил тот же абзац, и так три раза подряд.
    """
    reply = (
        "Ҷуброни пеш аз мӯҳлат аз рӯи фоизи дархостӣ ҳисоб мешавад, "
        "ва ин шарт барои ҳамаи амонатҳо амал мекунад [1]."
    )
    server = FakeSoro(reply=reply).start()
    monkeypatch.setattr(llm.settings, "SORO_API_URL", server.base_url, raising=False)
    try:
        first = dict(await ask(client, "Ҷуброн чӣ хел ҳисоб мешавад?", thread_id="t1"))
        second = dict(await ask(client, "бале", thread_id="t1"))
    finally:
        server.stop()

    assert first["final"]["text"] == reply
    assert second["final"]["escalated"]
    assert second["final"]["reason"] == llm.REASON_NO_ANSWER
    assert second["final"]["text"] != reply
    assert second["final"]["chunks_used"] == []


async def test_same_question_twice_gets_the_same_answer(client, knowledge, soro):
    """Клиент повторил вопрос — повтор ответа здесь правильный."""
    first = dict(await ask(client, "Фоизи амонат чанд аст?", thread_id="t1"))
    second = dict(await ask(client, "Фоизи амонат чанд аст?", thread_id="t1"))

    assert second["final"]["text"] == first["final"]["text"]
    assert not second["final"]["escalated"]


async def test_affirmation_searches_the_offered_topic(client, knowledge, monkeypatch):
    """«Бале» ищется как тема, которую предложил бот, и без вызова модели.

    Раньше на согласие поиск шёл по слову «бале» (оценка 0,001) и клиент
    получал оператора вместо продолжения разговора.
    """
    # Порог поднят: иначе «бале» само по себе проходит его по косинусу и
    # второй попытки не случается вовсе.
    monkeypatch.setattr(playground.settings, "RAG_MIN_SCORE", 0.5, raising=False)
    server = FakeSoro(
        reply=(
            "Фоизи солона 14,5% [1]. Оё мехоҳед дар бораи ҷуброни пеш аз "
            "мӯҳлат маълумот гиред?"
        )
    ).start()
    monkeypatch.setattr(llm.settings, "SORO_API_URL", server.base_url, raising=False)
    try:
        await ask(client, "Фоизи амонат чанд аст?", thread_id="t1")
        before = len(server.requests)
        events = dict(await ask(client, "бале", thread_id="t1"))
    finally:
        server.stop()

    retrieval = events["retrieval"]
    assert retrieval["rewritten"]
    assert retrieval["searched"] == (
        "Оё мехоҳед дар бораи ҷуброни пеш аз мӯҳлат маълумот гиред"
    )
    # ровно один новый запрос — генерация; переписыватель не звался
    assert len(server.requests) - before == 1


async def test_affirmation_reads_on_instead_of_escalating(
    client, knowledge, monkeypatch
):
    """«Бале» дочитывает документ, а не зовёт оператора.

    Бот сам заканчивает ответ вопросом «хотите узнать больше?». Если на
    «да» он отвечает «этих сведений у меня нет», он нарушает собственное
    обещание — самое заметное для клиента место во всём диалоге. Живой
    замер: тема набирала 0,549 при пороге 0,60, две тысячных решали.

    Здесь порог поднят до недостижимого, чтобы поиск по теме заведомо
    сорвался: проверяется именно запасной путь.
    """
    server = FakeSoro(
        reply=(
            "Фоизи солона 14,5% [1]. Оё мехоҳед дар бораи ҷуброни пеш аз "
            "мӯҳлат маълумот гиред?"
        )
    ).start()
    monkeypatch.setattr(llm.settings, "SORO_API_URL", server.base_url, raising=False)
    try:
        first = dict(await ask(client, "Фоизи амонат чанд аст?", thread_id="t1"))
        # Порог поднимаем ПОСЛЕ первого ответа: он должен был состояться,
        # иначе дочитывать будет нечего — бот ничего не процитировал.
        monkeypatch.setattr(
            playground.settings, "RAG_MIN_SCORE", 0.95, raising=False
        )
        events = dict(await ask(client, "бале", thread_id="t1"))
    finally:
        server.stop()

    shown = first["final"]["chunks_used"]
    assert shown, "первый ответ не сослался ни на один фрагмент"

    retrieval = events["retrieval"]
    assert retrieval["has_answer"], "согласие снова ушло оператору"
    assert retrieval["continued"], "дочитывание не помечено для «стеклянного ящика»"
    assert retrieval["best_score"] < retrieval["min_score"], (
        "тест перестал проверять запасной путь: тема взяла порог сама"
    )
    assert not events["final"]["escalated"]

    # Показываем ДРУГИЕ куски того же документа, а не пересказ прочитанного.
    offered = [f["chunk_id"] for f in retrieval["fragments"]]
    assert offered, "дочитывать оказалось нечего"
    assert not set(offered) & set(shown), "подсунули клиенту уже прочитанное"


# Случай «дочитывать нечего» проверяется в test_rag.py, а не здесь:
# площадка работает с демо-воркспейсом, где лежат настоящие документы
# стенда, и состав документа тесту не подконтролен.

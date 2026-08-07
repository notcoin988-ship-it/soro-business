"""Шестой шаг пути сообщения: поиск → модель → ответ (разделы 4 и 6.6 ТЗ).

`test_dialog.py` проверяет маршрут на пустой базе, `test_llm.py` — клиент
модели в отрыве. Здесь проверяется связка: база знаний наполнена
настоящими векторами, модель подставная, и смотрим, что бот отвечает по
документам, ссылается на них и уходит к оператору тогда, когда должен.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core import dialog, llm
from app.models import Chunk, Document, Message
from app.tests.fixture_llm import FakeSoro

WS = "test-ws"

FRAGMENTS = [
    (
        "Тарифҳои амонатҳо",
        1,
        "Амонати «Ояндасоз»: фоизи солона 14,5%, маблағи ҳадди ақал "
        "500 сомонӣ, мӯҳлат аз 12 то 36 моҳ.",
    ),
    (
        "Тарифҳои амонатҳо",
        2,
        "Ҷуброни пеш аз мӯҳлат аз рӯи фоизи «дархостӣ» — 0,5%.",
    ),
]


@pytest.fixture
async def knowledge(session, workspace):
    """База знаний тестового банка с настоящими эмбеддингами."""
    from app.ingest.worker import embed

    document = Document(
        workspace_id=workspace.id, kind="pdf", title=FRAGMENTS[0][0], status="ready"
    )
    session.add(document)
    await session.flush()

    vectors = await embed([text for _, _, text in FRAGMENTS])
    for (_, page, text), vector in zip(FRAGMENTS, vectors):
        session.add(
            Chunk(
                workspace_id=workspace.id,
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
        .where(Chunk.workspace_id == workspace.id)
        .values(tsv=func.to_tsvector("simple", func.lower(Chunk.text)))
    )
    await session.flush()
    return workspace


@pytest.fixture(autouse=True)
def reachable_threshold(monkeypatch):
    """Порог опущен намеренно — здесь проверяется связка, а не порог.

    Боевой порог откалиброван до 0,60, и таджикский вопрос про «Ояндасоз»
    (~0,63) его теперь проходит. Но привязывать тесты связки к точному
    значению всё равно не стоит: сдвинут порог ещё раз — и они начнут
    падать по причине, к связке отношения не имеющей. Поведение самого
    порога проверяется в `test_rag.py` и `test_dialog.py`.
    """
    monkeypatch.setattr(dialog.settings, "RAG_MIN_SCORE", 0.3, raising=False)


@pytest.fixture
def soro(monkeypatch):
    server = FakeSoro(
        reply="Фоизи солонаи амонати «Ояндасоз» 14,5% мебошад [1]."
    ).start()
    monkeypatch.setattr(llm.settings, "SORO_API_URL", server.base_url, raising=False)
    yield server
    server.stop()


async def send(session, text: str):
    return await dialog.handle_incoming(
        session, channel="telegram", external_id="tg-42", text=text, workspace_slug=WS
    )


async def test_bot_answers_from_documents(session, knowledge, soro):
    """Живой ответ вместо эха: текст приходит от модели."""
    reply = await send(session, "Фоизи амонати «Ояндасоз» чанд аст?")

    assert reply is not None
    assert "14,5%" in reply.text
    assert not reply.escalated


async def test_answer_carries_source_links(session, knowledge, soro):
    """Ссылка [1] превращается в chunks_used — из него канал строит бейдж.

    Без этого сноска в ответе некуда ведёт: раздел 6.6 требует, чтобы
    фронт получал chunks_used и рисовал подсказку с документом и страницей.
    """
    reply = await send(session, "Фоизи амонати «Ояндасоз» чанд аст?")

    assert reply.chunks_used, "ответ со ссылкой [1], а chunks_used пуст"
    chunk = await session.get(Chunk, reply.chunks_used[0])
    assert chunk is not None
    assert "Ояндасоз" in chunk.text


async def test_chunks_used_saved_to_message(session, knowledge, soro):
    """Инбокс оператора показывает найденные документы — они берутся отсюда."""
    await send(session, "Фоизи амонати «Ояндасоз» чанд аст?")

    outgoing = await session.scalar(
        select(Message)
        .where(Message.workspace_id == knowledge.id, Message.role == "assistant")
        .order_by(Message.id.desc())
    )
    assert outgoing.chunks_used


async def test_model_gets_fragments_not_raw_question(session, knowledge, soro):
    """В промпт уходят найденные фрагменты — иначе модели отвечать нечем."""
    await send(session, "Фоизи амонати «Ояндасоз» чанд аст?")

    sent = soro.requests[0]["messages"][1]["content"]
    assert "<docs>" in sent
    assert "Ояндасоз" in sent
    assert "[1] (Тарифҳои амонатҳо, стр. 1)" in sent


async def test_model_receives_masked_text(session, knowledge, soro):
    """Главный инвариант проекта: в модель уходит маска, не оригинал."""
    card = "5058123456789012"
    await send(session, f"Корти ман {card}, фоизи амонат чанд аст?")

    sent = soro.requests[0]["messages"][1]["content"]
    assert card not in sent
    assert "[CARD]" in sent


async def test_escalate_marker_hands_over_to_operator(session, knowledge, soro):
    """`[ESCALATE]` от модели переводит диалог оператору и режется из текста."""
    soro.reply = "Дастрасӣ надорам, мутахассисро пайваст мекунам. [ESCALATE]"

    reply = await send(session, "Фоизи амонати «Ояндасоз» чанд аст?")

    assert reply.escalated
    assert "[ESCALATE]" not in reply.text
    assert reply.reason in (llm.REASON_NO_ANSWER, llm.REASON_PII_TOPIC)


async def test_unavailable_model_does_not_leak_traceback(session, knowledge, soro):
    """Модель лежит — клиент видит вежливую фразу, а не 500.

    Боевой Soro живёт на чужом сервере и уже был недоступен; на демо это
    первое, что сломается.
    """
    soro.stop()

    reply = await send(session, "Фоизи амонати «Ояндасоз» чанд аст?")

    assert reply is not None
    assert reply.escalated
    assert reply.reason == "llm_unavailable"
    assert "Traceback" not in reply.text
    assert reply.text == dialog.LLM_DOWN_REPLY


# ---------------------------------------------------------------------------
# память диалога в каналах
# ---------------------------------------------------------------------------


async def test_channel_history_reaches_the_model(session, knowledge, soro):
    """Второй вопрос уходит в модель вместе с предыдущим обменом.

    В каналах история берётся из `messages` того же диалога, а не из
    памяти процесса: клиент может продолжить разговор из другого канала,
    и бот обязан помнить, о чём шла речь (раздел 9.3).
    """
    await send(session, "Фоизи амонати «Ояндасоз» чанд аст?")
    await send(session, "Ҷуброн чӣ хел ҳисоб мешавад?")

    messages = soro.requests[-1]["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert "Ояндасоз" in messages[1]["content"]


async def test_history_carries_masked_text_only(session, knowledge, soro):
    """В историю уходит `text_masked`, а не оригинал.

    Инвариант проекта: настоящий номер карты живёт только в
    `messages.text`. История отправляется в чужой сервис на КАЖДОМ
    следующем вопросе, поэтому здесь ошибка стоит дороже всего.
    """
    card = "5058123456789012"
    await send(session, f"Корти ман {card}, фоизи амонат чанд аст?")
    await send(session, "Ҷуброн чӣ хел?")

    import json

    assert card not in json.dumps(soro.requests, ensure_ascii=False)
    assert "[CARD]" in soro.requests[-1]["messages"][1]["content"]


async def test_first_question_has_no_history(session, knowledge, soro):
    """Первый вопрос диалога — как раньше: система + вопрос."""
    await send(session, "Фоизи амонати «Ояндасоз» чанд аст?")
    assert [m["role"] for m in soro.requests[-1]["messages"]] == ["system", "user"]


async def test_history_stops_at_conversation_boundary(session, knowledge, soro):
    """Чужой диалог в историю не попадает.

    Два контакта — два разговора. Смешать их значит показать одному
    клиенту вопросы другого; для банка это утечка.
    """
    await dialog.handle_incoming(
        session,
        channel="telegram",
        external_id="tg-99",
        text="Фоизи амонати «Ояндасоз» чанд аст?",
        workspace_slug=WS,
    )
    await send(session, "Ҷуброн чӣ хел?")

    assert [m["role"] for m in soro.requests[-1]["messages"]] == ["system", "user"]

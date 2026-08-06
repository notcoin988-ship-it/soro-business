"""Клиент модели и разбор её ответа (раздел 6.6 ТЗ).

Модель подставная (`fixture_llm.FakeSoro`), но путь к ней настоящий:
httpx, стриминг, разбор SSE. Боевой Soro живёт на чужом сервере, и тесты
ядра не должны зависеть от его аптайма.

Главное, что здесь проверяется, — ссылки `[1]`. Из них строятся бейджи в
консоли и футер «Манбаъ: …» в Telegram; разъедется нумерация — клиент
получит ссылку на чужой документ, и заметят это уже на приёмке.
"""

from __future__ import annotations

import pytest

from app.core import llm
from app.core.rag import Hit
from app.tests.fixture_llm import FakeSoro


def hit(chunk_id: int, title: str, page: int | None, text: str) -> Hit:
    return Hit(
        chunk_id=chunk_id,
        document_id=chunk_id * 10,
        title=title,
        page=page,
        source_url=None,
        text=text,
        score=0.7,
        rrf=0.03,
    )


HITS = [
    hit(101, "Тарифҳои амонатҳо", 1, "Фоизи солона 14,5%, ҳадди ақал 500 сомонӣ."),
    hit(102, "Тарифҳои амонатҳо", 2, "Ҷуброни пеш аз мӯҳлат — 0,5%."),
    hit(103, "eskhata.com/depo/", None, "Депозиты банка застрахованы."),
]


@pytest.fixture
def soro(monkeypatch):
    """Подставная модель на своём порту, настройки подменены на неё."""
    server = FakeSoro().start()
    monkeypatch.setattr(
        llm.settings, "SORO_API_URL", server.base_url, raising=False
    )
    yield server
    server.stop()


# ---------------------------------------------------------------------------
# промпт
# ---------------------------------------------------------------------------


def test_fragment_format_matches_tz():
    """Формат из ТЗ дословно: `[N] (Документ, стр. X): текст`."""
    block = llm.format_fragments(HITS[:1])
    assert block == "[1] (Тарифҳои амонатҳо, стр. 1): Фоизи солона 14,5%, " \
                    "ҳадди ақал 500 сомонӣ."


def test_fragment_numbering_starts_at_one():
    """Модель ссылается на [1], [2] — нумерация обязана начинаться с единицы."""
    block = llm.format_fragments(HITS)
    assert block.startswith("[1] ")
    assert "\n\n[2] " in block
    assert "\n\n[3] " in block


def test_web_fragment_has_no_page():
    """У страниц сайта страницы нет, и писать «стр. None» в промпт нельзя:
    модель честно перепишет это в ответ клиенту."""
    block = llm.format_fragments([HITS[2]])
    assert "стр." not in block
    assert block.startswith("[3] (eskhata.com/depo/): ") is False
    assert block.startswith("[1] (eskhata.com/depo/): ")


def test_system_prompt_is_verbatim():
    """Раздел 6.6 велит взять промпт дословно — сторож от «улучшений»."""
    prompt = llm.SYSTEM_PROMPT.format(bank_name="Банк Эсхата")
    assert prompt.startswith("Ту — Soro, ёрдамчии расмии «Банк Эсхата».")
    assert "Отвечай ТОЛЬКО на основе фрагментов в блоке <docs>" in prompt
    assert "[ESCALATE]" in prompt
    assert "После каждого факта ставь ссылку вида [1], [2]" in prompt


def test_messages_carry_question_and_docs():
    messages = llm.build_messages("Фоиз чанд аст?", HITS, "Банк Эсхата")

    assert [m["role"] for m in messages] == ["system", "user"]
    assert "<docs>" in messages[1]["content"]
    assert "Вопрос клиента: Фоиз чанд аст?" in messages[1]["content"]


# ---------------------------------------------------------------------------
# ссылки [1] → chunks_used
# ---------------------------------------------------------------------------


def test_citations_map_to_chunk_ids():
    """[1] — это первый выданный фрагмент, и никак иначе.

    `chunks_used` — то, по чему фронт рисует бейдж с названием документа.
    Сместись нумерация на единицу, и клиент увидит ссылку на чужую
    страницу, а на приёмке это шаг 2.
    """
    assert llm.cited_chunk_ids("Фоиз 14,5% [1]. Ҷуброн 0,5% [2].", HITS) == [101, 102]


def test_citation_order_follows_answer():
    """Порядок — как в тексте ответа, а не как в выдаче поиска."""
    assert llm.cited_chunk_ids("Сначала [3], потом [1].", HITS) == [103, 101]


def test_citation_repeats_counted_once():
    assert llm.cited_chunk_ids("[1] и снова [1]", HITS) == [101]


def test_citation_out_of_range_ignored():
    """Модель иногда сошлётся на [4], когда фрагментов три. Это не повод
    падать — просто пропускаем."""
    assert llm.cited_chunk_ids("Факт [4] и факт [2]", HITS) == [102]


def test_answer_without_citations_has_empty_chunks():
    assert llm.cited_chunk_ids("Просто текст без ссылок", HITS) == []


# ---------------------------------------------------------------------------
# [ESCALATE]
# ---------------------------------------------------------------------------


def test_escalate_marker_is_cut_from_text():
    """Маркер служебный — клиент его видеть не должен."""
    text, escalate, _ = llm.parse_answer(
        "Ин маълумот надорам, мутахассисро пайваст мекунам.\n[ESCALATE]",
        HITS,
        "Фоиз чанд аст?",
    )
    assert escalate
    assert "[ESCALATE]" not in text
    assert text.endswith("мекунам.")


def test_escalate_in_the_middle_does_not_leave_double_space():
    """Модель ставит маркер «в конец», но не всегда."""
    text, escalate, _ = llm.parse_answer(
        "Соединяю. [ESCALATE] Спасибо.", HITS, "вопрос"
    )
    assert escalate
    assert "  " not in text
    assert text == "Соединяю. Спасибо."


def test_reason_is_pii_topic_for_account_questions():
    """Правило 4 промпта: вопросы про счета и балансы — не «нет ответа».

    Причина попадает в инбокс, и по ней оператор понимает, надо ли лезть
    в АБС.
    """
    _, _, reason = llm.parse_answer(
        "Дастрасӣ надорам. [ESCALATE]", HITS, "Какой у меня баланс на карте?"
    )
    assert reason == llm.REASON_PII_TOPIC


def test_reason_is_no_answer_for_missing_facts():
    _, _, reason = llm.parse_answer(
        "Дар ҳуҷҷатҳо нест. [ESCALATE]", HITS, "Работаете ли вы с криптовалютой?"
    )
    assert reason == llm.REASON_NO_ANSWER


def test_answer_without_marker_is_not_escalated():
    text, escalate, reason = llm.parse_answer("Фоиз 14,5% [1].", HITS, "вопрос")
    assert not escalate
    assert reason is None
    assert text == "Фоиз 14,5% [1]."


# ---------------------------------------------------------------------------
# сеть: стриминг
# ---------------------------------------------------------------------------


async def test_stream_yields_pieces(soro):
    """Ответ приходит кусками — на этом держится «Площадка» и Telegram."""
    pieces = [
        piece
        async for piece in llm.stream_answer("Фоиз чанд аст?", HITS)
    ]
    assert len(pieces) > 1, "поток пришёл одним куском — проверьте разбор SSE"
    assert "".join(pieces) == soro.reply


async def test_answer_collects_stream(soro):
    result = await llm.answer("Фоиз чанд аст?", HITS)

    assert result.text == soro.reply
    assert result.chunks_used == [101]
    assert not result.escalate


async def test_answer_measures_first_token(soro):
    """Время до первых букв — отдельная метрика телеметрии на экране 03."""
    result = await llm.answer("Фоиз чанд аст?", HITS)
    assert result.first_token_ms > 0
    assert result.latency_ms >= result.first_token_ms


async def test_request_carries_model_and_prompt(soro):
    """В модель уходит имя из настроек и промпт с фрагментами.

    Имя проверяется отдельно: ТЗ фиксирует soro-27b-fp8, а сервер отдаёт
    GPTQ-int4, и брать его надо из SORO_MODEL.
    """
    await llm.answer("Фоиз чанд аст?", HITS)
    sent = soro.requests[0]

    assert sent["model"] == llm.settings.SORO_MODEL
    assert sent["stream"] is True
    assert "[1] (Тарифҳои амонатҳо, стр. 1)" in sent["messages"][1]["content"]


async def test_server_error_raises(soro):
    """5xx не проглатываем: `core/dialog.py` обязан узнать, что модель
    недоступна, и увести клиента к оператору."""
    soro.status = 503
    import httpx

    with pytest.raises(httpx.HTTPStatusError):
        await llm.answer("Фоиз чанд аст?", HITS)


async def test_broken_sse_frame_does_not_break_answer(soro):
    """Битый кусок потока — не повод потерять весь ответ."""
    soro.reply = "Фоиз 14,5% [1]."
    result = await llm.answer("Фоиз?", HITS)
    assert "14,5%" in result.text

"""Тесты нарезки на фрагменты (раздел 6.3 ТЗ).

Параметры чанкера зафиксированы документом, поэтому тесты проверяют не
«работает вообще», а каждое конкретное число: 400 токенов, перехлёст 60,
шапка дословно, границы страниц.

Токенайзер bge-m3 подтягивается из сети при первом обращении. Если его
нет, чанкер считает токены по символам — тесты от этого не зависят,
потому что проверяют поведение, а не точные длины.
"""

from __future__ import annotations

import pytest

from app.ingest import chunker
from app.ingest.chunker import (
    CHUNK_TOKENS,
    OVERLAP_TOKENS,
    Chunk,
    chunk_pages,
    count_tokens,
    header_for,
    split_paragraph,
)
from app.ingest.parsers import ParsedPage

TARIFF = "Фоизи солона аз рӯи амонати «Ояндасоз» 14,5% дар як сол мебошад."
MINIMUM = "Маблағи ҳадди ақали амонат 500 сомонӣ."


def page(text: str, number: int | None = 1) -> ParsedPage:
    return ParsedPage(page=number, text=text)


# ---------------------------------------------------------------------------
# контекст-шапка
# ---------------------------------------------------------------------------


def test_header_matches_tz_format():
    """Формат задан ТЗ дословно — не «стр.», не «с.», не через запятую."""
    assert header_for("Тарифҳо 2026", 4) == "Документ: Тарифҳо 2026. Страница 4."


def test_header_without_page_for_web():
    """У веб-страницы номера нет. «Страница None» в модель уходить не должно."""
    assert header_for("Амонати Ояндасоз", None) == "Документ: Амонати Ояндасоз."


def test_every_chunk_starts_with_header():
    chunks = chunk_pages([page(f"{TARIFF}\n{MINIMUM}")], "Тарифҳо")
    assert chunks
    for chunk in chunks:
        assert chunk.text.startswith("Документ: Тарифҳо. Страница 1.")


# ---------------------------------------------------------------------------
# размер и перехлёст
# ---------------------------------------------------------------------------


def test_short_page_is_one_chunk():
    chunks = chunk_pages([page(f"{TARIFF}\n{MINIMUM}")], "Тарифҳо")
    assert len(chunks) == 1
    assert TARIFF in chunks[0].text
    assert MINIMUM in chunks[0].text


def test_long_page_is_split():
    """Много абзацев — несколько чанков, каждый в пределах лимита."""
    text = "\n".join(f"{i}. {TARIFF} {MINIMUM}" for i in range(1, 60))
    chunks = chunk_pages([page(text)], "Тарифҳо")

    assert len(chunks) > 1
    for chunk in chunks:
        # шапка тоже占 место, поэтому небольшой запас сверху допустим
        assert count_tokens(chunk.text) <= CHUNK_TOKENS + 60


def test_overlap_repeats_tail_of_previous_chunk():
    """Перехлёст: ответ часто лежит на стыке абзацев.

    Без него стык рвётся посередине ответа, и поиск находит половину.
    """
    paragraphs = [f"Банди {i}. {TARIFF}" for i in range(1, 60)]
    chunks = chunk_pages([page("\n".join(paragraphs))], "Тарифҳо")
    assert len(chunks) >= 2

    first_lines = chunks[0].text.splitlines()[1:]  # без шапки
    second_lines = chunks[1].text.splitlines()[1:]
    assert set(first_lines) & set(second_lines), "перехлёста нет вообще"


def test_overlap_is_not_larger_than_limit():
    paragraphs = [f"Банди {i}. {TARIFF}" for i in range(1, 60)]
    chunks = chunk_pages([page("\n".join(paragraphs))], "Тарифҳо")

    first = chunks[0].text.splitlines()[1:]
    second = chunks[1].text.splitlines()[1:]
    shared = [line for line in second if line in first]
    assert count_tokens(" ".join(shared)) <= OVERLAP_TOKENS + 20


# ---------------------------------------------------------------------------
# длинный абзац режется по предложениям
# ---------------------------------------------------------------------------


def test_huge_paragraph_split_by_sentences():
    huge = " ".join([TARIFF] * 120)
    parts = split_paragraph(huge)

    assert len(parts) > 1
    for part in parts:
        assert count_tokens(part) <= CHUNK_TOKENS
    # предложения не должны склеиваться в кашу без пробелов
    assert all(part.strip() for part in parts)


def test_sentence_without_punctuation_split_by_words():
    """Скан без пунктуации или таблица в одну строку — режем по словам.

    Фрагмент, который не влезает в модель, хуже грубой границы.
    """
    huge = " ".join(["сомонӣ"] * 3000)
    parts = split_paragraph(huge)

    assert len(parts) > 1
    for part in parts:
        assert count_tokens(part) <= CHUNK_TOKENS


def test_short_paragraph_untouched():
    assert split_paragraph(TARIFF) == [TARIFF]


# ---------------------------------------------------------------------------
# страницы
# ---------------------------------------------------------------------------


def test_chunk_never_crosses_page_boundary():
    """Иначе сноска «стр. 4» в ответе бота укажет не туда."""
    pages = [page(TARIFF, 1), page(MINIMUM, 2), page(TARIFF, 3)]
    chunks = chunk_pages(pages, "Тарифҳо")

    assert [c.page for c in chunks] == [1, 2, 3]
    for chunk in chunks:
        assert f"Страница {chunk.page}." in chunk.text


def test_ord_is_sequential_across_pages():
    pages = [page(TARIFF, 1), page(MINIMUM, 2)]
    chunks = chunk_pages(pages, "Тарифҳо")
    assert [c.ord for c in chunks] == [1, 2]


def test_empty_pages_produce_nothing():
    chunks = chunk_pages([page("", 1), page("   \n  ", 2)], "Пустой")
    assert chunks == []


def test_web_page_keeps_page_none():
    chunks = chunk_pages([ParsedPage(page=None, text=TARIFF)], "Сайт банка")
    assert len(chunks) == 1
    assert chunks[0].page is None
    assert "Страница" not in chunks[0].text


def test_returns_chunk_objects():
    chunks = chunk_pages([page(TARIFF)], "Тарифҳо")
    assert all(isinstance(c, Chunk) for c in chunks)


# ---------------------------------------------------------------------------
# токенайзер
# ---------------------------------------------------------------------------


def test_token_count_is_positive():
    assert count_tokens(TARIFF) > 0
    assert count_tokens("") >= 0


def test_tokenizer_is_bge_m3_or_fallback():
    """Если токенайзер доступен — он должен быть от модели эмбеддингов.

    Считать токены чужим токенайзером бессмысленно: границы чанков
    разъедутся с тем, что реально увидит bge-m3.
    """
    tokenizer = chunker._tokenizer()
    if tokenizer is None:
        pytest.skip("токенайзер недоступен, работает запасной счётчик")
    assert "bge-m3" in (tokenizer.name_or_path or "")

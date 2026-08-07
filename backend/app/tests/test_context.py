"""Память диалога: история, переписывание вопроса, поиск второй попыткой.

Проверяется то, ради чего всё затевалось: бот задаёт уточняющий вопрос
(правило 7 промпта) и обязан понять ответ на него. Живой отказ, с
которого началось:

    Клиент:  салом чигуна корти милли дархост кунам
    Бот:     …Оё шумо аллакай барномаи «Эсхата Онлайн»-ро насб кардаед?
    Клиент:  не чи гуна насб кунам ?
    Бот:     Ин маълумот дар ҳуҷҷатҳои бонк нест…
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.core import context, llm
from app.core.context import Turn
from app.core.rag import Hit
from app.tests.fixture_llm import FakeSoro

HISTORY = [
    Turn("user", "чигуна корти милли дархост кунам"),
    Turn("assistant", "Оё шумо аллакай барномаи «Эсхата Онлайн»-ро насб кардаед?"),
]


@pytest.fixture
def soro(monkeypatch):
    """Подставная модель на месте боевой."""
    servers: list[FakeSoro] = []

    def factory(**kwargs):
        server = FakeSoro(**kwargs).start()
        monkeypatch.setattr(settings, "SORO_API_URL", server.base_url)
        servers.append(server)
        return server

    yield factory
    for server in servers:
        server.stop()


# ---------------------------------------------------------------------------
# сборка истории
# ---------------------------------------------------------------------------


def test_trim_keeps_last_turns():
    turns = [Turn("user", f"вопрос {n}") for n in range(20)]
    kept = context.trim(turns)

    assert len(kept) == context.HISTORY_TURNS
    assert kept[-1].text == "вопрос 19"


def test_trim_respects_char_budget():
    """Один длинный ответ бота не должен вытеснить блок <docs> из промпта."""
    turns = [Turn("assistant", "я" * 1000), Turn("user", "а сколько стоит?")]

    kept = context.trim(turns)

    assert sum(len(t.text) for t in kept) <= context.HISTORY_CHARS
    assert kept[-1].text == "а сколько стоит?", "свежую реплику потеряли"


def test_trim_drops_oversized_turn_whole():
    """Реплику, не влезающую целиком, выбрасываем, а не режем: полфразы в
    промпте хуже, чем её отсутствие."""
    kept = context.trim([Turn("user", "я" * (context.HISTORY_CHARS + 1))])
    assert kept == []


def test_trim_skips_empty():
    assert context.trim([Turn("user", "   "), Turn("user", "вопрос")]) == [
        Turn("user", "вопрос")
    ]


def test_dialogue_marks_speakers():
    lines = context.as_dialogue(HISTORY, "не чи гуна насб кунам ?").splitlines()

    assert lines[1].startswith("Клиент: чигуна")
    assert lines[2].startswith("Бот: Оё шумо")


def test_dialogue_ends_with_task_not_replica():
    """Последняя строка — задание, а не реплика клиента.

    Первая версия заканчивалась на «Клиент: …», и модель дописывала
    разговор: вместо переформулировки приходил ответ, который уходил
    прямо в поиск вместе с выдуманными «App Store» и «Google Play».
    """
    lines = context.as_dialogue(HISTORY, "не чи гуна насб кунам ?").splitlines()

    assert lines[-1] == "Реплика для переписывания: не чи гуна насб кунам ?"
    assert not lines[-1].startswith("Клиент:")


# ---------------------------------------------------------------------------
# история в промпте
# ---------------------------------------------------------------------------


HITS = [
    Hit(
        chunk_id=101,
        document_id=1,
        title="Барномаи мобилии «Эсхата Онлайн»",
        page=None,
        source_url=None,
        text="Барномаро аз App Store ё Google Play насб кунед.",
        score=0.6,
        rrf=0.03,
    )
]


def test_history_goes_into_prompt():
    messages = llm.build_messages("не чи гуна насб кунам ?", HITS, "Банк", HISTORY)

    assert [m["role"] for m in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert "Эсхата Онлайн" in messages[2]["content"]


def test_history_is_optional():
    """Без истории промпт ровно тот, что был: система + вопрос."""
    messages = llm.build_messages("вопрос", HITS, "Банк")
    assert [m["role"] for m in messages] == ["system", "user"]


def test_old_citations_are_stripped_from_history():
    """Номера прошлого ответа указывают на ДРУГИЕ фрагменты.

    Оставить «[2]» в истории значит показать модели пример, где двойка
    означает что-то иное, чем в нынешнем блоке <docs>, — и получить
    ссылку на чужой документ в новом ответе.
    """
    history = [
        Turn("user", "фоизи амонат чанд аст"),
        Turn("assistant", "Фоизи солона 14,5% [1]. Ҷуброн 0,5% [2]."),
    ]

    messages = llm.build_messages("а срок?", HITS, "Банк", history)

    assert "[1]" not in messages[2]["content"]
    assert "[2]" not in messages[2]["content"]
    assert "14,5%" in messages[2]["content"], "вместе со ссылками вырезали факт"


def test_client_citations_are_left_alone():
    """Из реплики КЛИЕНТА ничего не вырезаем: это его текст, а не наш."""
    history = [Turn("user", "что значит [1] в вашем ответе?")]
    messages = llm.build_messages("вопрос", HITS, "Банк", history)
    assert "[1]" in messages[1]["content"]


# ---------------------------------------------------------------------------
# переписывание вопроса
# ---------------------------------------------------------------------------


async def test_condense_rewrites_follow_up(soro):
    server = soro(condensed="чи гуна барномаи «Эсхата Онлайн»-ро насб кунам?")

    result = await llm.condense_question(HISTORY, "не чи гуна насб кунам ?")

    assert result == "чи гуна барномаи «Эсхата Онлайн»-ро насб кунам?"
    sent = server.requests[0]
    assert sent["stream"] is False, "переписывание не стримим"
    assert sent["temperature"] == 0.0, "нужен один и тот же вопрос при той же истории"


async def test_condense_without_history_does_not_call_model(soro):
    """Первый вопрос в диалоге переписывать не из чего — и незачем
    тратить на это вызов модели."""
    server = soro(condensed="что-нибудь")

    assert await llm.condense_question([], "фоизи амонат чанд аст") == (
        "фоизи амонат чанд аст"
    )
    assert server.requests == []


async def test_condense_survives_model_failure(soro):
    """Модель упала — ищем по исходной реплике. Переписывание
    необязательное, ронять из-за него ответ нельзя."""
    soro(status=503)
    assert await llm.condense_question(HISTORY, "не чи гуна насб кунам ?") == (
        "не чи гуна насб кунам ?"
    )


@pytest.mark.parametrize(
    "raw, expected",
    [
        # рассуждение после первой строки отбрасываем
        ("чи гуна насб кунам?\nЯ подставил предмет из истории.", "чи гуна насб кунам?"),
        ('"чи гуна насб кунам?"', "чи гуна насб кунам?"),
        ("«чи гуна насб кунам?»", "чи гуна насб кунам?"),
        ("   \n\nчи гуна насб кунам?", "чи гуна насб кунам?"),
    ],
)
async def test_condense_cleans_model_output(soro, raw, expected):
    soro(condensed=raw)
    assert await llm.condense_question(HISTORY, "не чи гуна?") == expected


async def test_condense_rejects_essay(soro):
    """Модель решила порассуждать одной строкой — считаем это неудачей.

    Разросшийся «вопрос» уводит поиск в чужие документы, и лучше искать
    по короткой реплике, чем по сочинению.
    """
    soro(condensed="слово " * 200)
    assert await llm.condense_question(HISTORY, "не чи гуна?") == "не чи гуна?"


async def test_condense_returns_reply_unchanged_when_clear(soro):
    """Правило 4 промпта: понятную реплику модель возвращает как есть."""
    soro()  # без `condensed` — фикстура эхом отдаёт реплику клиента
    assert await llm.condense_question(HISTORY, "фоизи амонат чанд аст") == (
        "фоизи амонат чанд аст"
    )


# ---------------------------------------------------------------------------
# заслон против выдумки в запросе
# ---------------------------------------------------------------------------


async def test_condense_rejects_invented_words(soro):
    """Сторож живой поломки: модель ответила вместо переформулировки.

    Настоящий прогон вернул вот это, и оно ушло в поиск как запрос:

        «Барои насб кардани барномаи… аз мағозаҳои Google Play… ё
         App Store… зеркашӣ кунед. Пас аз»

    Ни «App Store», ни «Google Play» в базе знаний нет ни одного раза —
    модель их сочинила. Кросс-энкодер затем честно оценил найденный
    фрагмент против этой выдумки в 0,902, и бот ответил «по документам»,
    которых не читал. Слова со стороны — признак сочинения, и по нему
    переписывание отбраковывается.
    """
    soro(
        condensed=(
            "Барои насб кардани барнома аз мағозаҳои Google Play "
            "ё App Store зеркашӣ кунед"
        )
    )

    assert await llm.condense_question(HISTORY, "не чи гуна насб кунам ?") == (
        "не чи гуна насб кунам ?"
    )


async def test_condense_allows_words_from_history(soro):
    """Переформулировка из слов истории проходит — ради этого всё и есть."""
    soro(condensed="чи гуна барномаи «Эсхата Онлайн»-ро насб кунам?")

    assert await llm.condense_question(HISTORY, "не чи гуна насб кунам ?") == (
        "чи гуна барномаи «Эсхата Онлайн»-ро насб кунам?"
    )


async def test_condense_tolerates_a_couple_of_link_words(soro):
    """Пара связок от модели — не повод отбраковывать.

    Ноль чужих слов требовать нельзя: морфология таджикского и русского
    не даёт совпасть точно, а «мехоҳед» или «хочу» модель вставляет
    законно.
    """
    soro(condensed="Оё шумо мехоҳед барномаи «Эсхата Онлайн»-ро насб кунед?")

    assert await llm.condense_question(HISTORY, "не чи гуна насб кунам ?") == (
        "Оё шумо мехоҳед барномаи «Эсхата Онлайн»-ро насб кунед?"
    )


async def test_condense_strips_example_label(soro):
    """Модель иногда копирует разметку примера: «Ответ: …»."""
    soro(condensed="Ответ: чи гуна барномаи «Эсхата Онлайн»-ро насб кунам?")

    assert await llm.condense_question(HISTORY, "не чи гуна?") == (
        "чи гуна барномаи «Эсхата Онлайн»-ро насб кунам?"
    )


async def test_condense_rejects_wordy_rewrite(soro):
    """Слишком много слов — уже не вопрос, а пересказ."""
    soro(condensed=" ".join(["насб"] * (llm.CONDENSE_MAX_WORDS + 1)))
    assert await llm.condense_question(HISTORY, "не чи гуна?") == "не чи гуна?"

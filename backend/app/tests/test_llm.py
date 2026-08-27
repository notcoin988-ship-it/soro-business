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


def test_system_prompt_keeps_tz_rules():
    """Правила ТЗ на месте — сторож от «улучшений» сверх согласованных."""
    prompt = llm.SYSTEM_PROMPT.format(bank_name="Банк Эсхата")
    assert prompt.startswith("Ту — Soro, ёрдамчии расмии «Банк Эсхата».")
    assert "Отвечай ТОЛЬКО на основе фрагментов в блоке <docs>" in prompt
    assert "[ESCALATE]" in prompt
    # правило 3 вернулось к формулировке ТЗ после провалившейся правки
    assert "После каждого факта ставь ссылку вида [1], [2]" in prompt
    assert "Не выдумывай числа" in prompt


def test_prompt_differs_from_tz_only_where_agreed():
    """Отход от дословного текста 6.6 — ровно в правилах 1, 2 и 7.

    Правила 1 и 7 согласованы с тимлидом после живого прогона: модель
    дописывала [ESCALATE] к нормальным ответам (4 раза из 5) и вставляла
    в текст пометки в скобках.

    Правило 2 переписано 14.08.2026. В версии ТЗ оно кончалось словами
    «смешанный -> как удобнее клиенту» — этим разрешением модель и
    пользовалась, выдавая один ответ дважды, по-таджикски и по-русски
    подряд. Теперь язык задаёт клиент, а дублировать запрещено прямо.

    Всё остальное обязано совпадать с ТЗ построчно — иначе «поправил
    одно, задел другое» пройдёт незамеченным.
    """
    ours = llm.SYSTEM_PROMPT.splitlines()
    tz = llm.SYSTEM_PROMPT_TZ.splitlines()

    changed = {line for line in tz if line not in ours}
    assert changed == {
        # правило 1
        "   Если ответа там нет — скажи, что соединишь со",
        "   специалистом, и добавь в конец строку [ESCALATE].",
        # правило 2
        "2. Отвечай на языке вопроса: таджикский -> таджикский,",
        "   русский -> русский, смешанный -> как удобнее клиенту.",
        # правило 7
        "7. В конце, где уместно, задай один уточняющий вопрос.",
    }


def test_user_template_is_verbatim():
    """Шаблон вопроса вернулся к ТЗ: попытка перечислить в нём
    разрешённые ссылки чинила формат, но ломала ответ — см. комментарий
    в llm.py."""
    assert "{fragments}" in llm.USER_TEMPLATE
    assert "Вопрос клиента: {question}" in llm.USER_TEMPLATE
    assert "Разрешённые ссылки" not in llm.USER_TEMPLATE


def test_messages_carry_question_and_docs():
    messages = llm.build_messages("Фоиз чанд аст?", HITS, "Банк Эсхата")

    assert [m["role"] for m in messages] == ["system", "user"]
    assert "<docs>" in messages[1]["content"]
    assert "Вопрос клиента: Фоиз чанд аст?" in messages[1]["content"]


# ---------------------------------------------------------------------------
# язык ответа
# ---------------------------------------------------------------------------


def test_language_is_ordered_explicitly_not_asked_for():
    """Язык клиента уходит в промпт приказом, а не надеждой на правило 2.

    Замер: на «привет хочу оформить кредить» модель отвечала по-таджикски
    4 раза из 4, хотя правило 2 требовало русского и все три фрагмента
    были с русских страниц. С этой строкой — 4 из 4 по-русски.
    """
    system = llm.build_messages("привет хочу оформить кредить", HITS, "Банк")[0]

    assert llm.LANGUAGE_ORDER["ru"] in system["content"]
    assert llm.LANGUAGE_ORDER["tg"] not in system["content"]
    # приказ идёт последним: ближайшее к концу указание держится крепче
    assert system["content"].rstrip().endswith(llm.LANGUAGE_ORDER["ru"])


def test_tajik_question_orders_tajik():
    system = llm.build_messages("чигуна корти милли дархост кунам", HITS, "Банк")[0]

    assert llm.LANGUAGE_ORDER["tg"] in system["content"]
    assert llm.LANGUAGE_ORDER["ru"] not in system["content"]


def test_unclear_language_leaves_the_prompt_alone():
    """Язык не определён — ведём себя как раньше.

    Приказать наугад хуже, чем промолчать: ответ уедет клиенту, и ошибка
    в приказе стоит дороже, чем её отсутствие.
    """
    system = llm.build_messages("visa 12345", HITS, "Банк")[0]

    assert llm.LANGUAGE_ORDER["ru"] not in system["content"]
    assert llm.LANGUAGE_ORDER["tg"] not in system["content"]


def test_prompt_forbids_answering_in_two_languages():
    """Правило 2 больше не разрешает «как удобнее клиенту».

    Именно эта формулировка позволяла модели выдать один ответ дважды,
    на таджикском и на русском подряд.
    """
    assert "как удобнее клиенту" not in llm.SYSTEM_PROMPT
    assert "на двух языках" in llm.SYSTEM_PROMPT


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


def test_grouped_citation_is_expanded():
    """«[1, 2]» — две ссылки в одной скобке.

    Живой ответ Soro на вопрос про «Корти миллӣ» заканчивался ровно так,
    и строгая форма `[N]` не видела эту скобку вовсе: `chunks_used`
    приходил пустым, клиент получал ответ без источников — нарушение
    раздела 6.5 ТЗ на ровном месте.
    """
    text = 'Дар шуъбаҳо ва дар "Эсхата Онлайн" дастрас аст. [1, 2]'
    assert llm.cited_chunk_ids(text, HITS) == [101, 102]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Факт [1,2].", [101, 102]),
        ("Факт [1;3].", [101, 103]),
        ("Факт [1, 2, 3].", [101, 102, 103]),
        # подпункт первого фрагмента — это по-прежнему один фрагмент
        ("Факт [1.1].", [101]),
        ("Факт [2.1] и [2.2].", [102]),
        # мусор в скобках ссылкой не считается
        ("Сумма [до 10 000] сомони", []),
        ("Пункт [а] договора", []),
    ],
)
def test_citation_shapes(text, expected):
    assert llm.cited_chunk_ids(text, HITS) == expected


def test_grouped_citation_is_stripped_when_sources_off():
    """Выключенный переключатель «Ссылка на источник» обязан убирать и
    групповую скобку — иначе в ответе останется висеть «[1, 2]»."""
    assert llm.strip_citations("Дастрас аст. [1, 2]") == "Дастрас аст."
    assert llm.strip_citations("Дастрас аст [1.1].") == "Дастрас аст."


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


@pytest.mark.parametrize(
    "question",
    [
        "Какой у меня баланс на карте?",
        "Почему у меня списали 90 сомони?",
        "Мою заявку на кредит одобрили?",
        "Разблокируйте мою карту пожалуйста",
        "Корти ман кор намекунад, лимити ман чанд аст?",
    ],
)
def test_personal_questions_are_pii_topic(question):
    assert llm.pii_topic(question)


@pytest.mark.parametrize(
    "question",
    [
        # Продуктовые вопросы: слова те же, но спрашивают про ТАРИФ, а не
        # про свои деньги. Первая версия правила ловила их все, и оператор
        # в инбоксе видел причину «нужен доступ к данным клиента» там, где
        # клиент просто спросил цену.
        "Сколько стоит открытие счёта физическому лицу?",
        "Комиссия за перевод в другой банк",
        "Какие карты вы выпускаете?",
        "Фоизи қарзи истеъмолӣ чанд аст?",
        "Какая ставка по депозиту?",
    ],
)
def test_product_questions_are_not_pii_topic(question):
    assert not llm.pii_topic(question)


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


# ---------------------------------------------------------------------------
# ответ без текста
# ---------------------------------------------------------------------------


async def test_answer_of_one_citation_becomes_escalation(soro):
    """Живой прогон: весь ответ модели — «[1]», без единого слова.

    Клиент получил бы пустое сообщение и решил, что бот сломался.
    Отдаём вежливую фразу и уводим к оператору.
    """
    soro.reply = "[1]"
    result = await llm.answer("Какой кэшбэк по карте Visa Gold?", HITS)

    assert result.escalate
    assert len(result.text) > 30
    assert result.text == llm.EMPTY_ANSWER_REPLY


async def test_answer_of_only_marker_becomes_escalation(soro):
    """То же для ответа из одного [ESCALATE]: маркер вырежется, а текста
    под ним нет."""
    soro.reply = "[ESCALATE]"
    result = await llm.answer("Банк работает с криптовалютой?", HITS)

    assert result.escalate
    assert result.text == llm.EMPTY_ANSWER_REPLY


async def test_normal_answer_is_not_replaced(soro):
    """Сторож от чрезмерной подозрительности: обычный ответ не трогаем."""
    soro.reply = "Комиссия не взимается [1]."
    result = await llm.answer("Сколько стоит открытие счёта?", HITS)

    assert not result.escalate
    assert result.text == "Комиссия не взимается [1]."

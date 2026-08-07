"""Гибридный поиск по базе знаний (раздел 6.4 ТЗ).

Тесты идут на настоящем PostgreSQL с pgvector и настоящих эмбеддингах
bge-m3: обе ветки поиска — это SQL и векторы, на моках здесь не
проверяется ничего. Индексация фрагментов делается напрямую, без воркера
и очереди: проверяем поиск, а не ingest.

Содержимое фрагментов подобрано под живой кейс демо — таджикский и
русский вперемешку, вклады, тарифы, курсы.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.rag import build_tsquery, embed_query, search
from app.models import Chunk, Document

# Фрагменты базы знаний тестового банка. Первый — целевой для вопроса про
# «Ояндасоз», остальные достаточно близки по теме, чтобы поиск не выглядел
# выбором из одного варианта.
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
        "Ҷуброни пеш аз мӯҳлат аз рӯи фоизи «дархостӣ» — 0,5% ҳисоб "
        "карда мешавад.",
    ),
    (
        "Тарифы обслуживания",
        1,
        "Открытие счёта физическим лицам — комиссия не взимается. "
        "Закрытие счёта — комиссия не взимается.",
    ),
    (
        "Тарифы обслуживания",
        4,
        "Переводы по системе платежей НБТ в пользу клиентов других "
        "банков — 5 сомони.",
    ),
    (
        "Курсы валют",
        1,
        "Курс доллара США на 6 августа 2026 года: покупка 10,90, "
        "продажа 11,05 сомони.",
    ),
]


@pytest.fixture
async def knowledge(session, workspace):
    """Наполняем базу знаний тестового воркспейса настоящими векторами."""
    from app.ingest.worker import embed

    document_ids: dict[str, int] = {}
    texts = [text for _, _, text in FRAGMENTS]
    vectors = await embed(texts)

    for (title, page, body), vector in zip(FRAGMENTS, vectors):
        if title not in document_ids:
            document = Document(
                workspace_id=workspace.id,
                kind="pdf",
                title=title,
                status="ready",
            )
            session.add(document)
            await session.flush()
            document_ids[title] = document.id

        session.add(
            Chunk(
                workspace_id=workspace.id,
                document_id=document_ids[title],
                page=page,
                ord=page,
                text=body,
                embedding=vector,
            )
        )
    await session.flush()

    # tsv считает база: to_tsvector нельзя передать параметром вставки
    await session.execute(
        Chunk.__table__.update()
        .where(Chunk.workspace_id == workspace.id)
        .values(tsv=func.to_tsvector("simple", func.lower(Chunk.text)))
    )
    await session.flush()
    return workspace


# ---------------------------------------------------------------------------
# разбор вопроса в tsquery
# ---------------------------------------------------------------------------


def test_tsquery_joins_words_with_or():
    """Слова через ИЛИ, иначе текстовая ветка мертва.

    `plainto_tsquery` соединил бы их через И — фрагмента, где встречаются
    разом все слова вопроса, не существует.
    """
    assert build_tsquery("Фоизи амонати Ояндасоз") == "фоизи | амонати | ояндасоз"


def test_tsquery_drops_punctuation():
    """Кавычки и знак вопроса — синтаксис tsquery, а не текст.

    Без очистки «Ояндасоз?» роняет запрос синтаксической ошибкой.
    """
    assert build_tsquery("«Ояндасоз» чанд аст?") == "ояндасоз | чанд | аст"


def test_tsquery_drops_one_letter_words():
    """Предлоги «в», «у», «ба» есть в каждом фрагменте и только шумят."""
    assert build_tsquery("вклад в банке") == "вклад | банке"


def test_tsquery_of_emoji_is_empty():
    """Вопрос без единого слова — не повод падать."""
    assert build_tsquery("👍👍👍") == ""


# ---------------------------------------------------------------------------
# поиск
# ---------------------------------------------------------------------------


async def test_finds_target_fragment_in_tajik(session, knowledge):
    """Главный вопрос демо: ставка по вкладу, спрошенная на таджикском.

    Проверяется именно ПОИСК — нужный фрагмент первым. Про порог см.
    следующий тест: с ним всё сложнее.
    """
    result = await search(
        session, "Фоизи амонати «Ояндасоз» чанд аст?", knowledge.id
    )

    assert result.hits, "поиск не вернул ничего"
    assert "Ояндасоз" in result.hits[0].text


async def test_calibrated_threshold_lets_tajik_answer_through(session, knowledge):
    """Таджикский ответ проходит порог. Раньше — нет.

    Здесь стоял сторож калибровки: при пороге ТЗ 0,65 правильный
    таджикский ответ набирал ~0,63 и отсекался, хотя поиск находил его
    первым. Сторож требовал упасть, когда порог откалибруют, — и упал.

    Порог опущен до нижней границы диапазона 3.2 (0,60) после того, как
    вторым фильтром заработала сама модель: правило 1 промпта велит ей
    отказаться, если ответа во фрагментах нет. Обоснование и замеры — в
    комментарии к `RAG_MIN_SCORE` в config.py.

    bge-m3 знает таджикский хуже русского, и все его оценки смещены вниз;
    именно поэтому демо-вопрос упирался в порог, а русские формулировки
    на той же базе давали 0,68.
    """
    from app.config import settings

    result = await search(
        session, "Фоизи амонати «Ояндасоз» чанд аст?", knowledge.id
    )

    assert 0.55 <= result.best_score <= 0.75, (
        f"близость таджикского ответа уехала: {result.best_score:.3f}. "
        "Проверьте модель эмбеддингов и текст фикстуры"
    )
    assert settings.RAG_MIN_SCORE == 0.60, "порог изменили — обновите этот тест"
    assert result.has_answer, (
        "таджикский ответ снова отсекается порогом — проверьте, не подняли "
        "ли RAG_MIN_SCORE обратно"
    )


async def test_returns_no_more_than_return_k(session, knowledge):
    """В промпт кладём ровно RAG_RETURN_K фрагментов (раздел 6.4)."""
    result = await search(session, "фоиз амонат сомонӣ", knowledge.id, return_k=3)
    assert len(result.hits) <= 3


async def test_hit_carries_footnote_fields(session, knowledge):
    """Из чего собирается сноска «[1] Тарифҳои амонатҳо, стр. 1»."""
    hit = (await search(session, "Ояндасоз фоиз", knowledge.id)).hits[0]

    assert hit.title == "Тарифҳои амонатҳо"
    assert hit.page == 1
    assert hit.chunk_id > 0 and hit.document_id > 0


async def test_score_is_cosine_similarity_not_rrf(session, knowledge):
    """Порог сравнивается с косинусной близостью — она в диапазоне 0..1.

    Главная ловушка 6.4: RRF при top_k=12 не превышает ~0,033, и если
    сравнить с ним порог 0,65, бот не ответит никогда.
    """
    result = await search(session, "Амонати Ояндасоз фоизи солона", knowledge.id)
    best = result.hits[0]

    assert 0.0 <= best.score <= 1.0
    assert best.score > best.rrf
    assert best.rrf < 0.05, "RRF внезапно стал большим — проверьте формулу"
    assert result.best_score == best.score


async def test_russian_question_finds_russian_fragment(session, knowledge):
    """Русский вопрос — русский фрагмент. Демо двуязычное."""
    result = await search(session, "сколько стоит открытие счёта", knowledge.id)
    assert "Открытие счёта" in result.hits[0].text


async def test_text_branch_finds_exact_number(session, knowledge):
    """Точная формулировка — то, ради чего вторая ветка и нужна.

    Вектор на «10,90» промахивается: цифры плохо ложатся в эмбеддинг,
    а `to_tsvector` находит их дословно.
    """
    result = await search(session, "10,90", knowledge.id)
    assert any("10,90" in hit.text for hit in result.hits)


async def test_unknown_question_has_no_answer(session, knowledge):
    """«Почему списали 90 сомони» — в документах этого нет.

    Ноль выдуманных ответов важнее recall: лучше лишняя эскалация.
    """
    result = await search(
        session, "Чаро аз ҳисоби ман 90 сомонӣ гирифта шуд?", knowledge.id
    )
    assert not result.has_answer


async def test_empty_question_returns_nothing(session, knowledge):
    """Пустое сообщение не должно ходить ни в модель эмбеддингов, ни в базу."""
    result = await search(session, "   ", knowledge.id)
    assert result.hits == []
    assert not result.has_answer
    assert result.best_score == 0.0


async def test_question_without_words_does_not_crash(session, knowledge):
    """Одни смайлики: текстовая ветка выключается, векторная работает.

    Без выключателя `to_tsquery('simple', '')` падает синтаксической
    ошибкой и роняет весь поиск.
    """
    result = await search(session, "👍👍👍", knowledge.id)
    assert isinstance(result.hits, list)


async def test_other_workspace_is_invisible(session, knowledge, demo_workspace):
    """Изоляция воркспейсов — требование ТЗ и главный риск для банка.

    Данные одного банка не должны находиться в поиске другого.
    """
    result = await search(
        session, "Фоизи амонати Ояндасоз", demo_workspace.id
    )
    ours = {hit.chunk_id for hit in result.hits}

    theirs = set(
        (
            await session.scalars(
                select(Chunk.id).where(Chunk.workspace_id == knowledge.id)
            )
        ).all()
    )
    assert not (ours & theirs)


async def test_empty_knowledge_base_has_no_answer(session, workspace):
    """Пустая база — не ошибка, а честное «ответа нет»."""
    result = await search(session, "Фоизи амонат чанд аст?", workspace.id)
    assert result.hits == []
    assert not result.has_answer


async def test_min_score_can_be_overridden(session, knowledge):
    """Порог крутится параметром: eval_rag.py калибрует его в 0,60–0,72
    без перезапуска бэкенда."""
    question = "Чаро аз ҳисоби ман 90 сомонӣ гирифта шуд?"

    assert not (await search(session, question, knowledge.id, min_score=0.9)).has_answer
    assert (await search(session, question, knowledge.id, min_score=0.0)).has_answer


async def test_embed_query_matches_schema_dimension():
    """Размерность вектора вопроса обязана совпасть со схемой, иначе
    `<=>` упадёт уже в SQL, и разбираться придётся по трейсу из базы."""
    from app.config import settings

    vector = await embed_query("Фоизи амонат")
    assert len(vector) == settings.EMBEDDINGS_DIM

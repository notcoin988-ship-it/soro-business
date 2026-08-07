"""Поиск фрагментов в базе знаний (раздел 6.4 ТЗ) — самое важное место проекта.

ОТВЕТСТВЕННОСТЬ: по вопросу клиента вернуть до `RAG_RETURN_K` фрагментов с
оценкой близости и решить, есть ли ответ вообще.

ВХОД:  текст вопроса, `workspace_id`.
ВЫХОД: список фрагментов (`chunks.id`, текст, документ, страница, score)
       и признак «ответа нет» — он запускает эскалацию.

КАК УСТРОЕН ПОИСК (менять только вместе с ТЗ):

* векторная ветка — `embedding <=> :qvec` по индексу hnsw;
* текстовая ветка — `to_tsvector('simple', lower(text))`; конфигурация
  именно `simple`, потому что таджикского словаря в PostgreSQL нет, а
  стемминг русского испортит таджикские слова;
* ветки сливаются формулой RRF `1/(60 + rank)` — одним SQL-запросом.

ГЛАВНАЯ ЛОВУШКА РАЗДЕЛА 6.4: порог `RAG_MIN_SCORE = 0.65` сравнивается
НЕ с RRF, а с косинусной близостью лучшего кандидата (`1 - расстояние`).
RRF годится только для сортировки: его значения не имеют смысла как
уверенность. При `RAG_TOP_K = 12` лучший возможный RRF равен
`1/61 + 1/61 ≈ 0,033` — сравнивать это с 0,65 значит никогда не отвечать.

ЗАВИСИМОСТИ: `models.Chunk`, эмбеддинги через `EMBEDDINGS_URL`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

# Константа RRF из ТЗ. 60 — значение из исходной статьи (Cormack et al.):
# оно сглаживает разницу между первым и вторым местом, чтобы одна ветка не
# забивала другую.
RRF_K = 60

# Поиск — интерактивный: норматив раздела 6 даёт на ВЕСЬ ответ 6 секунд, из
# которых основное съест модель. Поэтому свой таймаут, а не общий с
# индексацией (там `ingest/worker.py` ждёт до 600 секунд, и это правильно
# для фоновой очереди, но недопустимо здесь).
QUERY_TIMEOUT = httpx.Timeout(10.0, connect=3.0)

# Из вопроса берём только буквы и цифры. Всё остальное — кавычки, дефисы,
# вопросительные знаки — для `to_tsquery` синтаксис, и «Ояндасоз?» уронил бы
# запрос с syntax error.
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Буквы, которые есть в таджикской кириллице и которых нет в русской. Та же
# эвристика, что в SQL раздела 5.1 (приложение Б) — держим одинаковой,
# иначе аналитика и поиск будут по-разному считать язык.
TAJIK_LETTERS = frozenset("ӣӯҳҷғқӢӮҲҶҒҚ")

# ПРОБЛЕМА, КОТОРУЮ ЭТА ЭВРИСТИКА НЕ РЕШИЛА (не повторять вслепую).
#
# Документы банка почти все на русском, и bge-m3 систематически занижает
# близость, когда язык вопроса не совпадает с языком документа. Замер на
# боевой базе, один и тот же вопрос двумя языками:
#
#   «чигуна корти милли дархост кунам»   0,513   ← документ найден ПЕРВЫМ
#   «Как оформить карту Корти Милли?»    0,709
#   «ҳисоб кушодан чанд арзиш дорад»     0,514
#   «Сколько стоит открытие счёта?»      0,686
#
# Разрыв около 0,19, ранжирование при этом верное: нужный документ стоит
# первым в обоих случаях, страдает только абсолютное значение косинуса.
#
# Пробовали скидку к порогу для таджикских вопросов (0,10). Не сработало
# по двум причинам сразу:
#
# 1. Определять таджикский по буквам ӣ ӯ ҳ ҷ ғ қ нельзя. Ровно тот вопрос,
#    ради которого всё затевалось, — «чигуна корти милли дархост кунам» —
#    написан буквами, которые есть и в русском. Скидка на него не
#    распространилась, и он как эскалировал, так и эскалирует.
# 2. Зато скидка пропустила к модели вопрос про ЧУЖОЙ банк
#    («Дар Ориёнбонк фоизи қарз чанд аст?», 0,587), и модель выдумала
#    ответ вместе с несуществующей ссылкой на orionbank.tj — документа
#    этого банка в базе нет ни одного.
#
# То есть скидка не помогла там, где нужна, и навредила там, где не
# просили. Правильное решение — не двигать порог, а убрать перекос:
# двуязычные документы либо мультиязычный переранкер. Это отдельная
# задача, и она требует решения тимлида.
def is_tajik(text: str) -> bool:
    """Есть ли в тексте буквы, которых в русском алфавите не бывает.

    Осторожно: это НЕ определитель языка. Таджикская фраза без диакритики
    («чигуна корти милли дархост кунам») сюда не попадёт. Годится для
    грубой статистики (так же считает аналитика раздела 5.1), но не для
    решений, влияющих на ответ клиенту.
    """
    return bool(TAJIK_LETTERS & set(text))


@dataclass(frozen=True)
class Hit:
    """Один найденный фрагмент. Поля — то, из чего собирается сноска [1]."""

    chunk_id: int
    document_id: int
    title: str
    page: int | None
    source_url: str | None
    text: str
    # косинусная близость 0..1 — именно она показывается на «Площадке»
    score: float
    # значение RRF: годится только для сортировки, наружу не показываем
    rrf: float


@dataclass(frozen=True)
class RagResult:
    hits: list[Hit]
    # False → в базе знаний ответа нет, `core/escalation.py` зовёт оператора
    has_answer: bool
    # близость лучшего из возвращённых фрагментов; 0.0, если не нашлось ничего
    best_score: float


def build_tsquery(question: str) -> str:
    """Вопрос → выражение для `to_tsquery`, слова через ИЛИ.

    Почему не `plainto_tsquery`, который тут напрашивается: он соединяет
    слова через И. Вопрос «Фоизи амонати Ояндасоз чанд аст?» требовал бы
    фрагмента, где встречаются разом все пять слов, — таких не бывает, и
    текстовая ветка молча возвращала бы пусто на каждый живой вопрос.
    С ИЛИ отбор делает `ts_rank`, а не наличие всех слов.

    Однобуквенные токены выбрасываем: в таджикском и русском это предлоги
    («в», «у», «ба»), они есть в каждом фрагменте и только шумят.
    """
    tokens = [t.lower() for t in TOKEN_RE.findall(question) if len(t) > 1]
    return " | ".join(tokens)


# Один запрос вместо трёх обращений к базе: обе ветки и слияние считаются
# на стороне PostgreSQL. Гонять 24 кандидата в питон и сливать их там —
# лишний round-trip на каждом сообщении клиента.
#
# `:has_text` — выключатель текстовой ветки. Он нужен, потому что
# `to_tsquery('simple', '')` не возвращает пустой результат, а падает с
# синтаксической ошибкой: вопрос из одних смайликов уронил бы поиск.
SEARCH_SQL = text(
    """
    WITH vec AS (
        SELECT id,
               row_number() OVER (
                   ORDER BY embedding <=> CAST(:qvec AS vector)
               ) AS rank
        FROM chunks
        WHERE workspace_id = :ws
        ORDER BY embedding <=> CAST(:qvec AS vector)
        LIMIT :top_k
    ),
    txt AS (
        SELECT c.id,
               row_number() OVER (ORDER BY ts_rank(c.tsv, q.query) DESC) AS rank
        FROM chunks c, to_tsquery('simple', :tsq) AS q(query)
        WHERE :has_text
          AND c.workspace_id = :ws
          AND c.tsv @@ q.query
        ORDER BY ts_rank(c.tsv, q.query) DESC
        LIMIT :top_k
    ),
    merged AS (
        SELECT COALESCE(v.id, t.id) AS id,
               COALESCE(1.0 / (:rrf_k + v.rank), 0)
             + COALESCE(1.0 / (:rrf_k + t.rank), 0) AS rrf
        FROM vec v FULL OUTER JOIN txt t ON v.id = t.id
    )
    SELECT c.id            AS chunk_id,
           c.document_id   AS document_id,
           d.title         AS title,
           c.page          AS page,
           d.source_url    AS source_url,
           c.text          AS text,
           1 - (c.embedding <=> CAST(:qvec AS vector)) AS score,
           m.rrf           AS rrf
    FROM merged m
    JOIN chunks c    ON c.id = m.id
    JOIN documents d ON d.id = c.document_id
    ORDER BY m.rrf DESC, score DESC
    LIMIT :return_k
    """
)


async def embed_query(question: str) -> list[float]:
    """Вектор вопроса.

    Отдельно от `ingest.worker.embed` намеренно: там батч и таймаут в 10
    минут под фоновую индексацию, здесь — один текст и жёсткие 10 секунд.
    Тащить сюда воркер значило бы тянуть за ним парсеры и краулер.

    bge-m3 не требует приставки вроде «query: » — модель обучена без
    инструкций, и добавление префикса только сместило бы вектор.
    """
    url = settings.EMBEDDINGS_URL.rstrip("/") + "/embed"
    async with httpx.AsyncClient(timeout=QUERY_TIMEOUT) as client:
        response = await client.post(url, json={"inputs": [question]})
        response.raise_for_status()
        vectors = response.json()

    vector = vectors[0]
    if len(vector) != settings.EMBEDDINGS_DIM:
        raise ValueError(
            f"модель вернула вектор размерности {len(vector)}, "
            f"а в схеме {settings.EMBEDDINGS_DIM} — проверьте EMBEDDINGS_URL"
        )
    return vector


async def search(
    session: AsyncSession,
    question: str,
    workspace_id: int,
    *,
    top_k: int | None = None,
    return_k: int | None = None,
    min_score: float | None = None,
) -> RagResult:
    """Гибридный поиск по базе знаний одного воркспейса.

    Пороги вынесены в параметры не ради гибкости, а ради `eval_rag.py`:
    калибровка порога в диапазоне 0,60–0,72 (раздел 3.2) должна гоняться
    без перезапуска бэкенда.
    """
    top_k = top_k if top_k is not None else settings.RAG_TOP_K
    return_k = return_k if return_k is not None else settings.RAG_RETURN_K
    min_score = min_score if min_score is not None else settings.RAG_MIN_SCORE

    if not question.strip():
        return RagResult(hits=[], has_answer=False, best_score=0.0)

    vector = await embed_query(question)
    tsquery = build_tsquery(question)

    rows = (
        await session.execute(
            SEARCH_SQL,
            {
                # pgvector принимает вектор строкой; CAST в SQL приводит её
                # к типу vector
                "qvec": "[" + ",".join(repr(float(x)) for x in vector) + "]",
                "ws": workspace_id,
                # выключенной ветке всё равно нужен синтаксически верный
                # tsquery: PostgreSQL разбирает его до применения WHERE
                "tsq": tsquery or "пусто",
                "has_text": bool(tsquery),
                "top_k": top_k,
                "return_k": return_k,
                "rrf_k": RRF_K,
            },
        )
    ).mappings().all()

    hits = [
        Hit(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            title=row["title"],
            page=row["page"],
            source_url=row["source_url"],
            text=row["text"],
            score=float(row["score"]),
            rrf=float(row["rrf"]),
        )
        for row in rows
    ]

    # Порог — по лучшему из ВОЗВРАЩЁННЫХ фрагментов, а не по лучшему из
    # всех кандидатов: сказать «ответ есть», а в промпт положить другие
    # фрагменты — это и есть выдуманный ответ, которого ТЗ требует избежать.
    best_score = max((h.score for h in hits), default=0.0)
    return RagResult(
        hits=hits,
        has_answer=best_score >= min_score,
        best_score=best_score,
    )

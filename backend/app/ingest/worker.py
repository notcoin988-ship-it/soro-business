"""RQ-воркер индексации (разделы 6.1 и 6.3 ТЗ).

Задача `ingest_document(document_id)` — единственное место, где документ
превращается в строки таблицы `chunks`.

КОНВЕЙЕР:

 1. `documents.status = 'indexing'`;
 2. парсинг: `parsers.parse_file` для файла, `crawler.crawl` для сайта;
 3. нарезка `chunker.chunk_pages` — шапка и перехлёст по правилам 6.3;
 4. эмбеддинги: POST `EMBEDDINGS_URL/embed` батчами по 32;
 5. запись `chunks` вместе с `tsv = to_tsvector('simple', lower(text))`;
 6. `status='ready'`, `indexed_at=now()`;
 7. любая ошибка → `status='failed'`, текст в `documents.error`.

ПРОГРЕСС: `chunks_done`/`chunks_total` в `documents.settings` — колонки нет
в DDL раздела 5, добавлена нами, потому что её требует экран 02.

САЙТ = МНОГО ДОКУМЕНТОВ. `chunks.page` объявлен `INT`, а комментарий в DDL
обещает там «URL-хвост» — в INT он не влезает. Решено: одна строка
`documents` на каждую страницу сайта, URL в `source_url`, `page = NULL`.
Так ссылка в ответе бота ведёт на конкретную страницу, а DDL менять не
пришлось. Стартовый документ становится первой страницей обхода.

ПОЧЕМУ ЗАДАЧА СИНХРОННАЯ. RQ работает с обычными функциями, а весь наш
код асинхронный, поэтому точка входа синхронная и внутри крутит свой
event loop. Это нормально: воркер обрабатывает по одному документу.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import SessionLocal
from app.ingest.chunker import Chunk, chunk_pages
from app.ingest.crawler import crawl
from app.ingest.parsers import ParsedPage, parse_file
from app.models import Chunk as ChunkRow
from app.models import Document

log = logging.getLogger(__name__)

# Размер батча из ТЗ (6.3). Больше — упрёмся в память TEI, меньше — лишние
# круги по сети.
EMBED_BATCH = 32
# Индексация большого PDF идёт минутами: 40 страниц плотного текста — это
# около сотни фрагментов, каждый ~1 сек на CPU.
EMBED_TIMEOUT = httpx.Timeout(600.0)


def ingest_document(document_id: int) -> dict:
    """Точка входа для RQ."""
    return asyncio.run(_ingest(document_id))


async def _ingest(document_id: int, session: AsyncSession | None = None) -> dict:
    """Индексация. `session` принимается снаружи ради тестов: иначе задача
    открывает свою сессию, не видит данных теста и проверить её нечем."""
    if session is not None:
        return await _run(session, document_id)
    async with SessionLocal() as own:
        return await _run(own, document_id)


async def _run(session: AsyncSession, document_id: int) -> dict:
    document = await session.get(Document, document_id)
    if document is None:
        raise LookupError(f"документ {document_id} не найден")

    document.status = "indexing"
    document.error = None
    await session.commit()

    try:
        if document.kind == "web":
            return await _ingest_site(session, document)
        return await _ingest_file(session, document)
    except Exception as exc:  # noqa: BLE001 — причина должна дойти до человека
        await session.rollback()
        document = await session.get(Document, document_id)
        document.status = "failed"
        # текст ошибки уходит на экран 02: «не удалось» без причины
        # заставляет лезть в логи контейнера
        document.error = f"{type(exc).__name__}: {exc}"[:500]
        await session.commit()
        log.exception("индексация документа %s провалилась", document_id)
        raise


# ---------------------------------------------------------------------------
# файлы
# ---------------------------------------------------------------------------


async def _ingest_file(session: AsyncSession, document: Document) -> dict:
    path = Path(document.file_path or "")
    if not path.exists():
        raise FileNotFoundError(f"файл не найден: {path}")

    pages = await asyncio.to_thread(parse_file, path)
    document.pages = len(pages)

    chunks = chunk_pages(pages, document.title)
    await _store(session, document, chunks)

    return {
        "document_id": document.id,
        "pages": len(pages),
        "chunks": len(chunks),
        "ocr_pages": sum(1 for p in pages if p.ocr),
    }


# ---------------------------------------------------------------------------
# сайт
# ---------------------------------------------------------------------------


async def _ingest_site(session: AsyncSession, document: Document) -> dict:
    if not document.source_url:
        raise ValueError("для kind='web' нужен source_url")

    result = await crawl(document.source_url)
    if not result.pages:
        raise RuntimeError(
            "обход не дал ни одной страницы с текстом. "
            + next(
                (line for line in result.log if "ВНИМАНИЕ" in line),
                "проверьте адрес и robots.txt",
            )
        )

    total_chunks = 0
    for number, page in enumerate(result.pages):
        # первая страница ложится в исходный документ, остальные заводят
        # свои — так ссылка в ответе бота ведёт на конкретную страницу
        target = document
        if number > 0:
            target = Document(
                workspace_id=document.workspace_id,
                kind="web",
                title=page.title,
                source_url=page.url,
                status="indexing",
            )
            session.add(target)
            await session.flush()
        else:
            target.title = page.title or target.title
            target.source_url = page.url

        target.pages = 1
        chunks = chunk_pages([ParsedPage(page=None, text=page.text)], target.title)
        await _store(session, target, chunks)
        total_chunks += len(chunks)

    return {
        "document_id": document.id,
        "pages": len(result.pages),
        "chunks": total_chunks,
        "assets": len(result.assets),
        "crawl": {
            "fetched": result.stats.fetched,
            "stopped": result.stats.stopped_reason,
            "errors": result.stats.errors,
        },
    }


# ---------------------------------------------------------------------------
# эмбеддинги и запись
# ---------------------------------------------------------------------------


async def embed(texts: list[str]) -> list[list[float]]:
    """Векторы для фрагментов, батчами по 32 (правило 6.3)."""
    vectors: list[list[float]] = []
    url = settings.EMBEDDINGS_URL.rstrip("/") + "/embed"

    async with httpx.AsyncClient(timeout=EMBED_TIMEOUT) as client:
        for start in range(0, len(texts), EMBED_BATCH):
            batch = texts[start : start + EMBED_BATCH]
            response = await client.post(url, json={"inputs": batch})
            response.raise_for_status()
            vectors.extend(response.json())

    if vectors and len(vectors[0]) != settings.EMBEDDINGS_DIM:
        raise ValueError(
            f"модель вернула вектор размерности {len(vectors[0])}, "
            f"а в схеме {settings.EMBEDDINGS_DIM} — проверьте EMBEDDINGS_URL"
        )
    return vectors


async def _store(
    session: AsyncSession, document: Document, chunks: list[Chunk]
) -> None:
    """Эмбеддинги и запись фрагментов с прогрессом."""
    document.settings = {"chunks_done": 0, "chunks_total": len(chunks)}
    await session.commit()

    for start in range(0, len(chunks), EMBED_BATCH):
        batch = chunks[start : start + EMBED_BATCH]
        vectors = await embed([c.text for c in batch])

        await session.execute(
            insert(ChunkRow),
            [
                {
                    "workspace_id": document.workspace_id,
                    "document_id": document.id,
                    "page": chunk.page,
                    "ord": chunk.ord,
                    "text": chunk.text,
                    "embedding": vector,
                }
                for chunk, vector in zip(batch, vectors)
            ],
        )

        # tsv считаем на стороне базы отдельным запросом. Передать
        # to_tsvector() параметром пакетной вставки нельзя — это выражение
        # SQL, а не значение, asyncpg такое не принимает.
        #
        # Конфигурация 'simple' — из раздела 6.4: таджикского словаря в
        # PostgreSQL нет, а стемминг русского портит таджикские слова.
        await session.execute(
            update(ChunkRow)
            .where(ChunkRow.document_id == document.id, ChunkRow.tsv.is_(None))
            .values(tsv=func.to_tsvector("simple", func.lower(ChunkRow.text)))
        )

        # прогресс для экрана 02 — виден, пока идёт индексация
        document.settings = {
            "chunks_done": min(start + EMBED_BATCH, len(chunks)),
            "chunks_total": len(chunks),
        }
        await session.commit()

    document.status = "ready"
    document.indexed_at = datetime.now(tz=timezone.utc)
    await session.commit()


async def chunk_count(session: AsyncSession, document_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(ChunkRow).where(
            ChunkRow.document_id == document_id
        )
    )

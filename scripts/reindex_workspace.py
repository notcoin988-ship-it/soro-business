"""Переиндексация ВСЕХ страниц сайта в воркспейсе — по одной, на месте.

ЗАЧЕМ. Правила извлечения текста меняются (19.08.2026 в `parsers.DROP_TAGS`
добавлен `form`: сквозные бланки заявок засоряли индекс и вытесняли из
выдачи настоящие условия продуктов). Старые фрагменты в базе при этом
остаются прежними — их нужно пересобрать.

ПОЧЕМУ НЕ ПЕРЕОБХОД САЙТА. `ingest_document` для kind='web' зовёт
`crawl(source_url)` и заводит СОТНИ новых документов рядом со старыми.
Здесь, как в `reindex_page.py`, каждая страница обходится ровно одна, и
документ обновляется на месте: id, ссылки и статистика воркспейса целы.

Одна упавшая страница не роняет прогон: помечаем документ и идём дальше.

    docker compose exec backend python /code/scripts/reindex_workspace.py eskhata-demo
    docker compose exec backend python /code/scripts/reindex_workspace.py eskhata-demo --limit 5
"""

from __future__ import annotations

import argparse
import asyncio
import time

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.ingest.chunker import chunk_pages
from app.ingest.crawler import crawl
from app.ingest.parsers import ParsedPage
from app.ingest.worker import _store
from app.models import Chunk as ChunkRow
from app.models import Document, Workspace

# Пауза между страницами: обходим чужой прод банка, а не полигон.
DELAY_SEC = 0.5


async def reindex_one(session, document: Document) -> tuple[int, int]:
    """Одна страница → (было фрагментов, стало). Бросает при неудаче."""
    result = await crawl(
        document.source_url, max_pages=1, follow_language_variants=False
    )
    if not result.pages:
        raise RuntimeError("обход не дал текста")

    page = result.pages[0]
    removed = await session.execute(
        delete(ChunkRow).where(ChunkRow.document_id == document.id)
    )
    document.title = page.title or document.title
    document.source_url = page.url
    document.pages = 1
    document.error = None
    document.status = "ready"

    chunks = chunk_pages([ParsedPage(page=None, text=page.text)], document.title)
    await _store(session, document, chunks)
    return removed.rowcount or 0, len(chunks)


async def main(slug: str, limit: int | None) -> int:
    async with SessionLocal() as session:
        workspace = (
            await session.execute(select(Workspace).where(Workspace.slug == slug))
        ).scalar_one_or_none()
        if workspace is None:
            print(f"воркспейса «{slug}» нет")
            return 1

        query = (
            select(Document)
            .where(Document.workspace_id == workspace.id, Document.kind == "web")
            .order_by(Document.id)
        )
        if limit:
            query = query.limit(limit)
        documents = list((await session.execute(query)).scalars())

    print(f"воркспейс «{slug}»: страниц к переиндексации {len(documents)}\n")

    was = now = failed = 0
    started = time.monotonic()
    for number, stale in enumerate(documents, start=1):
        # Каждая страница — своя транзакция: падение одной не откатывает
        # уже переиндексированные.
        async with SessionLocal() as session:
            document = await session.get(Document, stale.id)
            try:
                before, after = await reindex_one(session, document)
                await session.commit()
                was += before
                now += after
                print(
                    f"[{number}/{len(documents)}] {document.title[:45]:45} "
                    f"{before:3} → {after:3}"
                )
            except Exception as error:  # noqa: BLE001 — прогон не должен падать
                await session.rollback()
                failed += 1
                print(f"[{number}/{len(documents)}] ОШИБКА {stale.source_url}: {error}")
        await asyncio.sleep(DELAY_SEC)

    spent = int(time.monotonic() - started)
    print(
        f"\nготово за {spent} сек: фрагментов {was} → {now}, "
        f"страниц с ошибкой {failed}"
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("slug", help="slug воркспейса, например eskhata-demo")
    parser.add_argument("--limit", type=int, help="только первые N страниц")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.slug, args.limit)))

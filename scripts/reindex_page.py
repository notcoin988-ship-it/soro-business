"""Переиндексация ОДНОЙ страницы сайта по id документа.

ЗАЧЕМ. Страницы сайта заводятся обходом: первая ложится в добавленный
документ, каждая следующая — в свой (`worker._ingest_site`). Если контейнер
умирает посередине — на этой машине WSL уносит все пять разом, см.
docker-compose.override.yml — такая страница навсегда остаётся в статусе
`indexing`: очередь пуста, задачу RQ пометил `AbandonedJobError`, а строку
в базе исправить больше некому. На экране 02 она вечно «индексируется».

ПОЧЕМУ НЕ ВЕРНУТЬ ЗАДАЧУ В ОЧЕРЕДЬ. Для `kind='web'` `ingest_document`
зовёт `crawl(source_url)`, то есть обойдёт весь сайт заново, стартовав с
этой страницы, и заведёт сотни новых документов. Здесь обход ограничен
одной страницей и не ходит по языковым версиям.

СТАРЫЕ ФРАГМЕНТЫ УДАЛЯЮТСЯ перед записью: `_store` только вставляет, и без
удаления повторный прогон удвоит фрагменты документа.

    docker compose exec backend python /code/scripts/reindex_page.py 4744
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import delete

from app.db import SessionLocal
from app.ingest.chunker import chunk_pages
from app.ingest.crawler import crawl
from app.ingest.parsers import ParsedPage
from app.ingest.worker import _store
from app.models import Chunk as ChunkRow
from app.models import Document


async def main(document_id: int) -> int:
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            print(f"документа {document_id} нет")
            return 1
        if document.kind != "web":
            print(f"документ {document_id} — kind={document.kind}, нужен web")
            return 1
        if not document.source_url:
            print(f"у документа {document_id} нет source_url")
            return 1

        print(f"страница: {document.source_url}")
        result = await crawl(
            document.source_url, max_pages=1, follow_language_variants=False
        )
        if not result.pages:
            document.status = "failed"
            document.error = "повторная индексация: страница не дала текста"
            await session.commit()
            print("обход не дал текста, документ помечен failed:")
            for line in result.log[-5:]:
                print("  " + line)
            return 1

        page = result.pages[0]
        removed = await session.execute(
            delete(ChunkRow).where(ChunkRow.document_id == document.id)
        )
        if removed.rowcount:
            print(f"удалено старых фрагментов: {removed.rowcount}")

        document.title = page.title or document.title
        document.source_url = page.url
        document.pages = 1
        document.error = None

        chunks = chunk_pages([ParsedPage(page=None, text=page.text)], document.title)
        await _store(session, document, chunks)
        print(f"готово: «{document.title}» — фрагментов {len(chunks)}")
        return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("document_id", type=int)
    raise SystemExit(asyncio.run(main(parser.parse_args().document_id)))

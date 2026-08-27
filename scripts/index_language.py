"""Доиндексация САЙТА НА ДРУГОМ ЯЗЫКЕ, когда язык переключается сессией.

ЗАЧЕМ. У trud.tj один и тот же URL отдаёт разный язык в зависимости от
cookie: `/set-language/ru` ставит её и редиректит обратно. Краулер ходит
без cookie и потому видит только таджикскую версию — а на встрече вопросы
задают и по-русски, и такой вопрос уходит в эскалацию, хотя ответ на сайте
есть.

ПОЧЕМУ НЕ ПРАВИМ КРАУЛЕР. Обычный обход идёт по ссылкам, а ссылки на всех
языках одинаковые: краулер пошёл бы по кругу и завёл дубли. Здесь список
страниц берётся из уже проиндексированных — обходить нечего, надо лишь
перечитать известное с другой cookie.

КАК РАЗЛИЧАЮТСЯ ДОКУМЕНТЫ. К `source_url` дописывается `?lang=<код>`.
Сайту этот параметр безразличен, а для базы адрес становится уникальным —
иначе русская версия затёрла бы таджикскую или легла бы дублем.

    docker compose exec backend python /code/scripts/index_language.py trud-demo ru
"""

from __future__ import annotations

import argparse
import asyncio

import httpx
from sqlalchemy import select

from app.db import SessionLocal
from app.ingest.chunker import chunk_pages
from app.ingest.parsers import ParsedPage, parse_html
from app.ingest.worker import _store
from app.models import Document, Workspace

USER_AGENT = "SoroBot/1.0 (+https://zehnlab.ai)"
# Вежливая пауза: это чужой прод, а не полигон.
DELAY_SEC = 0.4


async def main(slug: str, lang: str, limit: int | None) -> int:
    async with SessionLocal() as session:
        workspace = (
            await session.execute(select(Workspace).where(Workspace.slug == slug))
        ).scalar_one_or_none()
        if workspace is None:
            print(f"воркспейса «{slug}» нет")
            return 1

        query = (
            select(Document)
            .where(
                Document.workspace_id == workspace.id,
                Document.kind == "web",
                # Уже переведённые страницы второй раз не берём.
                ~Document.source_url.like("%lang=%"),
            )
            .order_by(Document.id)
        )
        if limit:
            query = query.limit(limit)
        originals = [(d.id, d.source_url) for d in (await session.execute(query)).scalars()]

    print(f"воркспейс «{slug}»: страниц к переводу на «{lang}» — {len(originals)}\n")

    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        # Переключаем язык один раз: cookie живёт в клиенте и уходит со
        # всеми следующими запросами.
        switch = await client.get(f"http://www.trud.tj/set-language/{lang}")
        print(f"переключение языка: HTTP {switch.status_code}, cookies={dict(client.cookies)}\n")

        added = failed = 0
        for number, (_, url) in enumerate(originals, start=1):
            marked = f"{url}{'&' if '?' in url else '?'}lang={lang}"
            try:
                response = await client.get(url)
                response.raise_for_status()
                title, text = parse_html(response.text)
                if not text.strip():
                    raise RuntimeError("страница не дала текста")

                async with SessionLocal() as session:
                    existing = (
                        await session.execute(
                            select(Document).where(
                                Document.workspace_id == workspace.id,
                                Document.source_url == marked,
                            )
                        )
                    ).scalar_one_or_none()
                    if existing is not None:
                        print(f"[{number}/{len(originals)}] уже есть: {marked}")
                        continue

                    document = Document(
                        workspace_id=workspace.id,
                        kind="web",
                        title=f"{title or url} · {lang.upper()}",
                        source_url=marked,
                        status="ready",
                        pages=1,
                    )
                    session.add(document)
                    await session.flush()

                    chunks = chunk_pages([ParsedPage(page=None, text=text)], document.title)
                    await _store(session, document, chunks)
                    await session.commit()
                    added += 1
                    print(f"[{number}/{len(originals)}] {document.title[:45]:45} фрагментов {len(chunks)}")
            except Exception as error:  # noqa: BLE001 — прогон не должен падать
                failed += 1
                print(f"[{number}/{len(originals)}] ОШИБКА {url}: {error}")
            await asyncio.sleep(DELAY_SEC)

    print(f"\nготово: добавлено {added}, с ошибкой {failed}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("slug")
    parser.add_argument("lang", help="код языка: ru, en, tj")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.slug, args.lang, args.limit)))

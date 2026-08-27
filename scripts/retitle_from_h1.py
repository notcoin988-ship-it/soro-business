"""Осмысленные названия документов вместо одинакового <title> сайта.

ЗАЧЕМ. У trud.tj все страницы отдают один и тот же <title> — «Mehnat».
Краулер честно берёт его, и в ответе клиенту список источников выглядит
так: «[1] Mehnat · RU, [2] Mehnat · RU, [3] Mehnat · RU». Ссылка на
источник — главный довод демо («смотрите, откуда взято»), и в таком виде
она не доказывает ничего.

Заголовок первого уровня на этих же страницах содержательный:
«Комплексное страхование автотранспортных средств», «Страхование
имущества». Его и ставим.

ЧАНКИ ТОЖЕ ОБНОВЛЯЮТСЯ: чанкер вписывает название документа первой
строкой каждого фрагмента («Документ: …»), и без правки поиск продолжит
находить старое имя внутри текста.

    docker compose exec backend python /code/scripts/retitle_from_h1.py trud-demo
"""

from __future__ import annotations

import argparse
import asyncio
import re

import httpx
from sqlalchemy import func, select, update

from app.db import SessionLocal
from app.models import Chunk, Document, Workspace

HEADING = re.compile(r"<h1[^>]*>(.*?)</h1>|<h2[^>]*>(.*?)</h2>", re.S | re.I)
TAGS = re.compile(r"<[^>]+>")


def heading_of(html: str) -> str:
    for match in HEADING.finditer(html):
        raw = match.group(1) or match.group(2) or ""
        text = " ".join(TAGS.sub("", raw).split())
        # Слишком короткое — это подпись или иконка, не заголовок страницы.
        if 3 < len(text) <= 90:
            return text
    return ""


async def main(slug: str) -> int:
    async with SessionLocal() as session:
        workspace = (
            await session.execute(select(Workspace).where(Workspace.slug == slug))
        ).scalar_one_or_none()
        if workspace is None:
            print(f"воркспейса «{slug}» нет")
            return 1
        documents = list(
            (
                await session.execute(
                    select(Document).where(
                        Document.workspace_id == workspace.id,
                        Document.kind == "web",
                    ).order_by(Document.id)
                )
            ).scalars()
        )

    print(f"страниц: {len(documents)}\n")
    renamed = skipped = 0

    async with httpx.AsyncClient(
        timeout=30, follow_redirects=True, headers={"User-Agent": "SoroBot/1.0"}
    ) as client:
        # Язык переключается сессией — берём тот же, что у страницы.
        await client.get("http://www.trud.tj/set-language/ru")

        for number, stale in enumerate(documents, start=1):
            url = (stale.source_url or "").split("?")[0]
            if not url:
                skipped += 1
                continue
            try:
                response = await client.get(url)
                title = heading_of(response.text)
                if not title:
                    print(f"[{number}] заголовка нет: {url}")
                    skipped += 1
                    continue

                suffix = " · RU" if "lang=ru" in (stale.source_url or "") else ""
                fresh = f"{title}{suffix}"
                if fresh == stale.title:
                    skipped += 1
                    continue

                async with SessionLocal() as session:
                    document = await session.get(Document, stale.id)
                    old = document.title
                    document.title = fresh
                    # Первая строка каждого фрагмента — «Документ: <имя>».
                    # Не поправить её значит оставить старое имя в тексте,
                    # по которому и ищет поиск.
                    await session.execute(
                        update(Chunk)
                        .where(Chunk.document_id == document.id)
                        .values(
                            text=func.replace(
                                Chunk.text,
                                f"Документ: {old}.",
                                f"Документ: {fresh}.",
                            )
                        )
                    )
                    await session.commit()

                renamed += 1
                print(f"[{number}] {fresh}")
            except Exception as error:  # noqa: BLE001
                print(f"[{number}] ОШИБКА {url}: {error}")
                skipped += 1
            await asyncio.sleep(0.3)

    print(f"\nпереименовано {renamed}, пропущено {skipped}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("slug")
    raise SystemExit(asyncio.run(main(parser.parse_args().slug)))

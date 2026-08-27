"""Наполнить стенд данными для проверки: воркспейс и документы.

ЗАЧЕМ. Пустой стенд бесполезен для тестировщика: бот на любой вопрос
отвечает «этой информации нет в документах», и непонятно, поломка это или
правильное поведение. Обход сайта банка занимает десятки минут, поэтому
здесь берутся три PDF, которые уже лежат в репозитории (`app/tests/data`,
настоящие документы банка) — их хватает и на поиск, и на сноски.

ЧТО ДЕЛАЕТ:
  1. заводит воркспейс, если его нет;
  2. кладёт документы в очередь индексации, если их там ещё нет;
  3. ждёт, пока воркер их разберёт, и печатает, что получилось.

ПОВТОРНЫЙ ЗАПУСК БЕЗОПАСЕН. Документ с тем же названием второй раз не
грузится: тестировщик, запустивший скрипт дважды, получит то же самое, а
не шесть копий тарифов.

    docker compose exec backend python scripts/seed_demo.py
    docker compose exec backend python scripts/seed_demo.py --ws bank-two
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, "/code")

from app.api.console import enqueue  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Chunk, Document, Workspace  # noqa: E402

PDF_DIR = Path("/code/app/tests/data")

# Сколько ждём индексацию. Три небольших PDF на CPU укладываются в две
# минуты; больше ждать незачем — значит, воркер не поднят.
TIMEOUT_SEC = 300


async def ensure_workspace(session, slug: str, name: str) -> Workspace:
    workspace = await session.scalar(select(Workspace).where(Workspace.slug == slug))
    if workspace is not None:
        print(f"воркспейс {slug} уже есть")
        return workspace

    workspace = Workspace(slug=slug, name=name)
    session.add(workspace)
    await session.commit()
    print(f"воркспейс {slug} заведён")
    return workspace


async def ensure_documents(session, workspace: Workspace) -> list[int]:
    files = sorted(PDF_DIR.glob("*.pdf"))
    if not files:
        print(f"в {PDF_DIR} нет PDF — нечего загружать", file=sys.stderr)
        return []

    folder = Path(settings.UPLOAD_DIR) / workspace.slug
    folder.mkdir(parents=True, exist_ok=True)

    queued = []
    for path in files:
        title = path.stem
        exists = await session.scalar(
            select(Document).where(
                Document.workspace_id == workspace.id, Document.title == title
            )
        )
        if exists is not None:
            print(f"  {title}: уже загружен ({exists.status})")
            continue

        # Копируем файл рядом с остальными загрузками: индексатор читает
        # его по пути из базы, а исходник в репозитории трогать нельзя.
        target = folder / f"{title}.pdf"
        target.write_bytes(path.read_bytes())

        document = Document(
            workspace_id=workspace.id,
            kind="pdf",
            title=title,
            file_path=str(target),
            status="queued",
        )
        session.add(document)
        await session.flush()
        queued.append(document.id)
        print(f"  {title}: поставлен в очередь")

    await session.commit()
    for document_id in queued:
        enqueue(document_id)
    return queued


async def wait_ready(session, workspace: Workspace) -> bool:
    started = time.monotonic()
    while time.monotonic() - started < TIMEOUT_SEC:
        documents = (
            await session.scalars(
                select(Document).where(Document.workspace_id == workspace.id)
            )
        ).all()
        for document in documents:
            await session.refresh(document)

        pending = [d for d in documents if d.status in ("queued", "indexing")]
        failed = [d for d in documents if d.status == "failed"]
        if not pending:
            for document in failed:
                print(f"  ОШИБКА {document.title}: {document.error}")
            return not failed

        print(f"  ждём индексацию: осталось {len(pending)}")
        await asyncio.sleep(5)

    print("индексация не закончилась — поднят ли воркер?", file=sys.stderr)
    return False


async def main(args) -> int:
    async with SessionLocal() as session:
        workspace = await ensure_workspace(session, args.ws, args.name)
        await ensure_documents(session, workspace)
        ok = await wait_ready(session, workspace)

        chunks = await session.scalar(
            select(Chunk).where(Chunk.workspace_id == workspace.id).limit(1)
        )
        total = len(
            (
                await session.scalars(
                    select(Chunk.id).where(Chunk.workspace_id == workspace.id)
                )
            ).all()
        )

    print(f"\nворкспейс {args.ws}: фрагментов в базе знаний — {total}")
    if not total or chunks is None:
        print("база знаний пуста: бот будет эскалировать любой вопрос", file=sys.stderr)
        return 1

    print("готово. Проверочные вопросы, на которые ответ ЕСТЬ:")
    print("  · Какая комиссия за перевод внутри банка?")
    print("  · Мӯҳлати пасандози мӯҳлатнок чанд рӯз аст?")
    print("и вопрос, на который ответа быть НЕ должно (уйдёт оператору):")
    print("  · Почему у меня списали 90 сомони вчера вечером?")
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Наполнить стенд для проверки")
    parser.add_argument("--ws", default=settings.WORKSPACE_DEFAULT_SLUG)
    parser.add_argument("--name", default="ДЕМО банк")
    raise SystemExit(asyncio.run(main(parser.parse_args())))

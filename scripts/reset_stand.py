"""Очистить стенд: пустая база для показа с нуля.

ЗАЧЕМ. Демонстрация «смотрите, как это работает» начинается с пустого
экрана: заводим банк, грузим документы, задаём вопрос. Стенд, на котором
уже лежат чужие диалоги и сотня документов, для этого не годится — на
экране 06 висят старые эскалации, на 07 чужие цифры, и рассказ спотыкается.

БЕЗ ДАМПА НЕ ЧИСТИТ. Перед удалением снимается `pg_dump` — обход сайта
банка на сотню страниц занимает десятки минут, и восстанавливать его
руками ради «случайно нажал» никто не захочет. Дамп кладётся рядом с
обычными бэкапами, восстановление — как в DEPLOY.md.

    docker compose exec backend python scripts/reset_stand.py            # покажет, что удалит
    docker compose exec backend python scripts/reset_stand.py --yes      # удалит
    docker compose exec backend python scripts/reset_stand.py --yes --keep-knowledge

`--keep-knowledge` оставляет документы и фрагменты: диалоги, эскалации и
аналитика обнуляются, а бот продолжает отвечать по базе знаний. Так
показывают «как бот работает», а не «как его настраивают с нуля».
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select, text

sys.path.insert(0, "/code")

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    AuditLog,
    ChannelIdentity,
    Chunk,
    Contact,
    Conversation,
    Document,
    Escalation,
    Feedback,
    Message,
    Workspace,
)

# Порядок обязателен: сначала то, что ссылается, потом то, на что
# ссылаются. Иначе внешние ключи не дадут удалить.
DIALOG_TABLES = (Feedback, Message, Escalation, Conversation, ChannelIdentity, Contact)
KNOWLEDGE_TABLES = (Chunk, Document)

BACKUP_DIR = Path("/backups")


def dump() -> Path | None:
    """Снять дамп перед чисткой. `None` — не получилось."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    name = BACKUP_DIR / f"before-reset-{datetime.now():%Y-%m-%d-%H%M}.dump"

    # Разбираем DATABASE_URL руками: pg_dump не понимает схему
    # `postgresql+asyncpg://`, которую требует SQLAlchemy.
    url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    try:
        subprocess.run(
            ["pg_dump", "-Fc", "-f", str(name), url],
            check=True,
            capture_output=True,
            timeout=600,
        )
    except FileNotFoundError:
        # Обычное дело: в образе бэкенда стоит Python, а `pg_dump` живёт в
        # образе базы. Не гадаем, а называем команду, которая сработает.
        print(
            "pg_dump здесь нет — он в контейнере базы. Снимите дамп так:\n"
            "  docker compose exec -T db pg_dump -U soro -Fc soro > backup.dump\n"
            "и повторите с --no-dump",
            file=sys.stderr,
        )
        return None
    except subprocess.CalledProcessError as exc:
        print(f"дамп не снялся: {exc.stderr.decode()[:200]}", file=sys.stderr)
        return None
    except subprocess.TimeoutExpired:
        print("дамп не уложился в 10 минут", file=sys.stderr)
        return None
    return name


async def counts(session) -> dict[str, int]:
    result = {}
    for model in (*DIALOG_TABLES, *KNOWLEDGE_TABLES, AuditLog, Workspace):
        result[model.__tablename__] = await session.scalar(
            select(func.count()).select_from(model)
        )
    return result


async def main(args) -> int:
    async with SessionLocal() as session:
        before = await counts(session)

    print("сейчас в базе:")
    for table, count in before.items():
        print(f"  {table:<20} {count}")

    if not args.yes:
        print("\nничего не удалено. Повторите с --yes, если это то, что нужно.")
        return 0

    if not args.no_dump:
        path = dump()
        if path is None:
            print("\nБЕЗ ДАМПА НЕ ЧИЩУ. Поправьте pg_dump или добавьте --no-dump.")
            return 1
        print(f"\nдамп снят: {path}")

    async with SessionLocal() as session:
        tables = list(DIALOG_TABLES)
        if not args.keep_knowledge:
            tables += list(KNOWLEDGE_TABLES)

        for model in tables:
            await session.execute(text(f"DELETE FROM {model.__tablename__}"))
        await session.execute(text("DELETE FROM audit_log"))

        if not args.keep_workspaces:
            # Воркспейсы удаляем последними и не трогаем тот, что в .env:
            # без него бэкенд не ответит ни на один запрос («воркспейс не
            # заведён»), и стенд выглядел бы сломанным, а не пустым.
            await session.execute(
                text("DELETE FROM workspaces WHERE slug <> :keep"),
                {"keep": settings.WORKSPACE_DEFAULT_SLUG},
            )
        await session.commit()

        after = await counts(session)

    print("\nстало:")
    for table, count in after.items():
        print(f"  {table:<20} {count}")

    if args.keep_knowledge:
        print("\nБаза знаний оставлена: бот отвечает, диалогов и цифр нет.")
    else:
        print(
            "\nСтенд пуст. Дальше: экран 02 — загрузить документы, "
            "или `python scripts/seed_demo.py` — положить три PDF банка."
        )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Очистить стенд для показа")
    parser.add_argument("--yes", action="store_true", help="действительно удалить")
    parser.add_argument(
        "--keep-knowledge",
        action="store_true",
        help="оставить документы и фрагменты",
    )
    parser.add_argument(
        "--keep-workspaces",
        action="store_true",
        help="оставить заведённые банки",
    )
    parser.add_argument(
        "--no-dump",
        action="store_true",
        help="не снимать дамп (по умолчанию снимается)",
    )
    raise SystemExit(asyncio.run(main(parser.parse_args())))

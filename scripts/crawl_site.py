"""Живой прогон краулера по сайту — лог для сдачи недели 2.

Критерий сдачи: «crawler.py проходит по тестовому сайту с соблюдением всех
лимитов (показать лог: сколько страниц, какая глубина, где остановился, что
отсёк robots.txt)». Автотесты гоняют краулер по локальному полигону, а этот
скрипт — по настоящему сайту, чтобы лог был не синтетический.

Запуск (из корня soro-business):
    docker compose exec backend python scripts/crawl_site.py https://eskhata.tj/
    docker compose exec backend python scripts/crawl_site.py https://eskhata.tj/ \\
        --max-pages 20 --out /tmp/crawl.log

Лимиты по умолчанию — ровно те, что в ТЗ (150 страниц, глубина 3,
задержка 0,5 сек). Уменьшать их у чужого сайта вежливо: полный обход
150 страниц с паузой 0,5 сек — это больше минуты нагрузки на прод банка.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, "/code")

from app.ingest.crawler import (  # noqa: E402
    DELAY_SEC,
    MAX_DEPTH,
    MAX_PAGES,
    crawl,
    format_report,
)


async def run(args) -> int:
    print(f"Старт: {args.url}")
    print(
        f"Лимиты: страниц {args.max_pages}, глубина {args.max_depth}, "
        f"задержка {args.delay} сек\n"
    )
    result = await crawl(
        args.url,
        max_pages=args.max_pages,
        max_depth=args.max_depth,
        delay=args.delay,
    )

    report = format_report(result)
    print(report)

    if result.assets:
        print(f"\nНайдено файлов (не качаем, решение 3): {len(result.assets)}")
        for url in result.assets[:20]:
            print(f"  {url}")
        if len(result.assets) > 20:
            print(f"  … и ещё {len(result.assets) - 20}")

    if result.pages:
        longest = max(result.pages, key=lambda p: len(p.text))
        print(
            f"\nСамая содержательная страница: {longest.url}\n"
            f"«{longest.title}», {len(longest.text)} символов\n"
            f"--- первые 400 символов ---\n{longest.text[:400]}"
        )

    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\nЛог записан: {args.out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES)
    parser.add_argument("--max-depth", type=int, default=MAX_DEPTH)
    parser.add_argument("--delay", type=float, default=DELAY_SEC)
    parser.add_argument("--out", help="куда положить лог")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

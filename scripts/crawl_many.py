"""Обход нескольких реальных сайтов подряд — проверка краулера на чужой вёрстке.

Полигон и eskhata.tj закрывают два случая: полностью предсказуемый и один
конкретный. Ни тот, ни другой не отвечает на вопрос «а на чужом сайте с
другой CMS оно вообще работает?». Здесь берём несколько банков
Таджикистана и смотрим, где краулер спотыкается.

Лимиты намеренно ниже боевых: чужие сайты, продакшен, и цель — проверить
поведение, а не собрать базу. robots.txt соблюдается, пауза 0,5 сек.

Запуск (из корня soro-business):
    docker compose exec backend python scripts/crawl_many.py
    docker compose exec backend python scripts/crawl_many.py --max-pages 10
    docker compose exec backend python scripts/crawl_many.py --sites https://alif.tj/
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

sys.path.insert(0, "/code")

from app.ingest.crawler import crawl  # noqa: E402

SITES = [
    "https://eskhata.tj/",
    "https://amonatbonk.tj/",
    "https://orienbank.tj/",
    "https://tsb.tj/",
    "https://ibt.tj/",
    "https://alif.tj/",
    "https://humo.tj/",
    "https://arvand.tj/",
]

TJ_LETTERS = "ӣӯҳҷғқӢӮҲҶҒҚ"


async def one(url: str, max_pages: int, verify: bool = True) -> dict:
    started = time.monotonic()
    row: dict = {"site": url}
    try:
        result = await crawl(url, max_pages=max_pages, verify=verify)
    except Exception as exc:  # чужой сайт может быть недоступен — не падаем
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row

    chars = sum(len(p.text) for p in result.pages)
    tj = sum(p.text.count(ch) for p in result.pages for ch in TJ_LETTERS)

    row.update(
        {
            "fetched": result.stats.fetched,
            "indexed": result.stats.indexed,
            "errors": result.stats.errors,
            "robots": result.stats.skipped_robots,
            "dup_url": result.stats.skipped_dup,
            "dup_text": result.stats.skipped_dup_text,
            "assets": len(result.assets),
            "pdfs": sum(1 for a in result.assets if a.lower().endswith(".pdf")),
            "canonical": sum(1 for line in result.log if "[canonical]" in line),
            "chars": chars,
            "tj_share": (tj / chars * 100) if chars else 0.0,
            "stopped": result.stats.stopped_reason,
            "sec": time.monotonic() - started,
            "moved": next(
                (line for line in result.log if "переехал" in line), ""
            ),
        }
    )
    return row


async def run(args) -> int:
    rows = []
    for url in args.sites:
        print(f"→ {url}", flush=True)
        row = await one(url, args.max_pages, verify=not args.insecure)
        rows.append(row)
        if "error" in row:
            print(f"  ОШИБКА: {row['error']}")
        else:
            print(
                f"  {row['indexed']} стр., {row['chars']} симв., "
                f"тадж. {row['tj_share']:.1f}%, PDF {row['pdfs']}, "
                f"{row['sec']:.0f} сек"
            )
        if row.get("moved"):
            print(f"  {row['moved']}")

    print("\n" + "=" * 108)
    head = (
        f"{'сайт':<26}{'запр':>5}{'стр':>5}{'ошиб':>6}{'robots':>7}"
        f"{'дубль URL':>10}{'копий':>7}{'canon':>6}{'PDF':>5}"
        f"{'тадж %':>8}{'сек':>6}  остановка"
    )
    print(head)
    print("-" * 108)
    for row in rows:
        name = row["site"].replace("https://", "").rstrip("/")
        if "error" in row:
            print(f"{name:<26}  {row['error'][:70]}")
            continue
        print(
            f"{name:<26}{row['fetched']:>5}{row['indexed']:>5}{row['errors']:>6}"
            f"{row['robots']:>7}{row['dup_url']:>10}{row['dup_text']:>7}"
            f"{row['canonical']:>6}{row['pdfs']:>5}{row['tj_share']:>8.1f}"
            f"{row['sec']:>6.0f}  {row['stopped']}"
        )
    print("=" * 108)

    ok = sum(1 for r in rows if r.get("indexed"))
    print(f"\nсайтов обошли успешно: {ok} из {len(rows)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sites", nargs="*", default=SITES)
    parser.add_argument("--max-pages", type=int, default=30)
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="не проверять TLS-сертификат: только для диагностики, решение "
        "по боевому использованию принимает тимлид",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

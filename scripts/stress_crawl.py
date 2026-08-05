"""Нагрузочный прогон краулера на большом синтетическом сайте.

Боевой обход по сайту банка показывает, как краулер ведёт себя в реальной
жизни, но проверить на нём предельные режимы нельзя: чужой сервер, чужая
вёрстка, сеть. Здесь наоборот — сайт свой, сети нет, зато страниц много и
все ловушки собраны нарочно:

* дерево с ветвлением и перекрёстными ссылками (страницы ссылаются назад
  и вбок, а не только вниз) — проверка, что обход в ширину не зациклится;
* у каждой страницы четыре варианта одного адреса (слэш, якорь, utm,
  порядок параметров) — проверка дедупликации под нагрузкой;
* цепочка редиректов, битые ссылки, не-HTML;
* глубина заведомо больше трёх.

Проверяются инварианты, которые на маленьком полигоне не видны:

1. запросов ровно `MAX_PAGES` — ни одним больше;
2. ни одна страница не скачана дважды;
3. глубже `MAX_DEPTH` не ушли;
4. память не растёт бесконтрольно (очередь и множества видимых адресов).

Запуск (из корня soro-business):
    docker compose exec backend python scripts/stress_crawl.py
    docker compose exec backend python scripts/stress_crawl.py --pages 2000
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
import time
import tracemalloc
from collections import Counter

sys.path.insert(0, "/code")

from app.ingest.crawler import (  # noqa: E402
    MAX_DEPTH,
    MAX_PAGES,
    crawl,
)
from app.tests.fixture_site import FixtureSite, Route, html  # noqa: E402

BRANCHING = 6  # ссылок вниз с каждой страницы


def build_big_site(total: int) -> dict[str, Route]:
    """Дерево из `total` страниц: ветвление 6, перекрёстные ссылки, дубли."""
    routes: dict[str, Route] = {
        "/robots.txt": Route(
            body=b"User-agent: *\nDisallow: /private\n",
            content_type="text/plain; charset=utf-8",
        ),
        "/private": Route(body=html("<p>Служебное</p>")),
        "/broken": Route(status=404, body=b"nope", content_type="text/plain"),
        "/report.pdf": Route(body=b"%PDF-1.4", content_type="application/pdf"),
        "/hop1": Route(status=302, location="/hop2"),
        "/hop2": Route(status=302, location="/p1"),
    }

    for i in range(1, total + 1):
        children = [
            f"/p{i * BRANCHING + k}"
            for k in range(BRANCHING)
            if i * BRANCHING + k <= total
        ]
        # ссылки вниз плюс перекрёстные назад: цикл, на котором наивный
        # обход без множества «уже видели» ушёл бы в бесконечность
        back = [f"/p{max(1, i // 2)}", f"/p{max(1, i - 1)}", "/p1"]
        links = "".join(f"<a href='{u}'>вниз {u}</a>" for u in children)
        links += "".join(f"<a href='{u}'>назад {u}</a>" for u in back)
        # четыре написания одного и того же адреса
        twin = children[0] if children else "/p1"
        links += (
            f"<a href='{twin}/'>дубль слэш</a>"
            f"<a href='{twin}#top'>дубль якорь</a>"
            f"<a href='{twin}?utm_source=fb'>дубль utm</a>"
            f"<a href='{twin}?b=2&a=1'>с параметрами</a>"
            f"<a href='{twin}?a=1&b=2'>те же параметры иначе</a>"
        )
        links += (
            "<a href='/private'>запрещено robots</a>"
            "<a href='/broken'>битая</a>"
            "<a href='/report.pdf'>файл</a>"
            "<a href='/hop1'>цепочка редиректов</a>"
            "<a href='https://example.org/x'>чужой домен</a>"
        )
        routes[f"/p{i}"] = Route(
            body=html(
                f"<h1>Саҳифаи {i}</h1><p>Фоизи солона — 14,5%. "
                f"Маблағи ҳадди ақал — 500 сомонӣ.</p>{links}",
                title=f"Страница {i}",
            )
        )

    routes["/"] = Route(
        body=html(
            "<h1>Асосӣ</h1>"
            + "".join(f"<a href='/p{i}'>раздел {i}</a>" for i in range(1, BRANCHING + 1)),
            title="Главная",
        )
    )
    return routes


async def run(args) -> int:
    routes = build_big_site(args.pages)
    site = FixtureSite(routes).start()

    print(f"полигон: {len(routes)} маршрутов, ветвление {BRANCHING}")
    print(f"лимиты:  страниц {args.max_pages}, глубина {MAX_DEPTH}, задержка 0\n")

    tracemalloc.start()
    started = time.monotonic()
    try:
        result = await crawl(
            site.base_url, max_pages=args.max_pages, max_depth=MAX_DEPTH, delay=0
        )
    finally:
        elapsed = time.monotonic() - started
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        hits = dict(site.hits)
        site.stop()

    requests = sum(v for k, v in hits.items() if k != "/robots.txt")
    depth_max = max((p.depth for p in result.pages), default=0)

    urls = [p.url for p in result.pages]
    dup_urls = len(urls) - len(set(urls))
    digests = Counter(
        hashlib.sha1(p.text.encode("utf-8")).hexdigest() for p in result.pages
    )
    dup_text = sum(v - 1 for v in digests.values() if v > 1)

    print(f"успешных запросов:   {result.stats.fetched}")
    print(f"обращений к серверу: {requests} (включая 404 и редиректы)")
    print(f"страниц с текстом:   {result.stats.indexed}")
    print(f"максимальная глубина:{depth_max}")
    print(f"дубли URL отсечены:  {result.stats.skipped_dup}")
    print(f"копии по тексту:     {result.stats.skipped_dup_text}")
    print(f"robots.txt отсёк:    {result.stats.skipped_robots}")
    print(f"чужой домен:         {result.stats.skipped_offdomain}")
    print(f"не-HTML:             {result.stats.skipped_asset}")
    print(f"ошибок:              {result.stats.errors}")
    print(f"остановка:           {result.stats.stopped_reason}")
    print(f"время:               {elapsed:.2f} сек "
          f"({elapsed / max(result.stats.fetched, 1) * 1000:.1f} мс/страница)")
    print(f"пик памяти:          {peak / 1024 / 1024:.1f} МБ")

    print("\nИНВАРИАНТЫ")
    checks = [
        (
            f"запросов ровно {args.max_pages}",
            result.stats.fetched == args.max_pages,
            f"было {result.stats.fetched}",
        ),
        (
            "ни один URL не проиндексирован дважды",
            dup_urls == 0,
            f"повторов: {dup_urls}",
        ),
        (
            "нет двух документов с одинаковым текстом",
            dup_text == 0,
            f"копий: {dup_text} из {len(urls)}",
        ),
        (
            f"глубина не больше {MAX_DEPTH}",
            depth_max <= MAX_DEPTH,
            f"было {depth_max}",
        ),
        (
            "запрещённое robots.txt не тронуто",
            hits.get("/private", 0) == 0,
            f"обращений к /private: {hits.get('/private', 0)}",
        ),
        (
            "остановились по лимиту, а не по пустой очереди",
            result.stats.stopped_reason == "max_pages",
            result.stats.stopped_reason,
        ),
    ]

    failed = 0
    for name, ok, detail in checks:
        print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + ("" if ok else f" — {detail}"))
        failed += not ok

    print()
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pages", type=int, default=1000, help="сколько страниц на полигоне"
    )
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

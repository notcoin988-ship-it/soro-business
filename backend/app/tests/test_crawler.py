"""Тесты краулера (неделя 2, раздел 6.2 ТЗ).

Каждый лимит из ТЗ проверяется отдельно: обход в ширину, только свой
домен, 150 страниц, глубина 3, задержка 0,5 сек, robots.txt. Плюс решения,
которые ТЗ оставило на исполнителя (дедупликация, редиректы, не-HTML,
таймауты и ретраи, title, кодировки) — они перечислены в шапке crawler.py
и здесь зафиксированы тестами, чтобы «решили и забыли» не случилось.

Полигон — локальный сайт из fixture_site.py, сеть не нужна.
"""

from __future__ import annotations

import time
from urllib.parse import urlsplit

import pytest

from app.ingest import crawler
from app.ingest.crawler import (
    CrawledPage,
    clean_url,
    crawl,
    format_report,
    is_skipped_path,
    language_variants,
    looks_like_asset,
    normalize_url,
    same_domain,
)
from app.tests.fixture_site import FixtureSite, Route, build_routes, html


@pytest.fixture
def site():
    server = FixtureSite(build_routes()).start()
    yield server
    server.stop()


@pytest.fixture
async def result(site):
    """Полный обход полигона с дефолтными лимитами, но без пауз."""
    return await crawl(site.base_url, delay=0)


def paths(result) -> set[str]:
    from urllib.parse import urlsplit

    return {urlsplit(p.url).path for p in result.pages}


# ---------------------------------------------------------------------------
# нормализация URL — решение 1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://bank.tj/tarify", "https://bank.tj/tarify"),
        ("https://bank.tj/tarify/", "https://bank.tj/tarify"),
        ("https://bank.tj/tarify#rates", "https://bank.tj/tarify"),
        ("https://bank.tj/tarify?utm_source=fb", "https://bank.tj/tarify"),
        ("https://bank.tj/tarify?gclid=123", "https://bank.tj/tarify"),
        ("https://BANK.tj/tarify", "https://bank.tj/tarify"),
        ("https://bank.tj:443/tarify", "https://bank.tj/tarify"),
        ("http://bank.tj:80/tarify", "http://bank.tj/tarify"),
        ("https://bank.tj", "https://bank.tj/"),
        ("https://bank.tj/", "https://bank.tj/"),
        # значимые параметры остаются, но порядок канонизируется.
        # Именно значимые: `page` отсюда убран намеренно — это пагинация,
        # см. test_pagination_param_is_dropped
        (
            "https://bank.tj/news?city=khujand&lang=tj",
            "https://bank.tj/news?city=khujand&lang=tj",
        ),
        (
            "https://bank.tj/news?lang=tj&city=khujand",
            "https://bank.tj/news?city=khujand&lang=tj",
        ),
        # трекинг вычищается, полезное остаётся
        (
            "https://bank.tj/news?utm_medium=cpc&lang=tj",
            "https://bank.tj/news?lang=tj",
        ),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        # clean_url НЕ трогает хвостовой слэш: для сервера это другой адрес
        ("https://bank.tj/news/", "https://bank.tj/news/"),
        ("https://bank.tj/news", "https://bank.tj/news"),
        # всё остальное чистит так же, как normalize_url
        ("https://BANK.tj/news/?utm_source=fb#top", "https://bank.tj/news/"),
        ("https://bank.tj:443/news/", "https://bank.tj/news/"),
    ],
)
def test_clean_url_keeps_trailing_slash(raw, expected):
    """Ключ сравнения и адрес запроса — разные вещи.

    Боевой прогон: eskhata.com отдаёт 200 на `/events/esg/eco-otvetstvennyy/`
    и 404 на тот же адрес без слэша. Пока краулер ходил по ключам, он сам
    себе сделал 160 «битых ссылок» и недобрал страницы.
    """
    assert clean_url(raw) == expected


def test_normalize_and_clean_agree_on_identity():
    """Оба варианта написания дают ОДИН ключ, но РАЗНЫЕ адреса запроса."""
    assert normalize_url("https://bank.tj/news/") == normalize_url(
        "https://bank.tj/news"
    )
    assert clean_url("https://bank.tj/news/") != clean_url("https://bank.tj/news")


def test_same_domain_rejects_subdomain():
    """Поддомен — чужой сайт (решение зафиксировано в crawler.py)."""
    assert same_domain("https://bank.tj/a", "https://bank.tj/")
    assert not same_domain("https://blog.bank.tj/a", "https://bank.tj/")
    assert not same_domain("https://example.org/a", "https://bank.tj/")


@pytest.mark.parametrize(
    "url, is_asset",
    [
        ("https://bank.tj/files/tarify.pdf", True),
        ("https://bank.tj/f/otchet.XLSX", True),
        ("https://bank.tj/img/logo.png", True),
        ("https://bank.tj/tarify", False),
        ("https://bank.tj/news?file=pdf", False),
    ],
)
def test_looks_like_asset(url, is_asset):
    assert looks_like_asset(url) is is_asset


# ---------------------------------------------------------------------------
# базовый обход
# ---------------------------------------------------------------------------


async def test_crawl_collects_site_pages(result):
    assert paths(result) >= {"/", "/tarify", "/about", "/tarify/details"}
    assert all(isinstance(p, CrawledPage) for p in result.pages)


async def test_crawl_extracts_main_text_only(result):
    page = next(p for p in result.pages if p.url.endswith("/tarify"))
    assert "14,5%" in page.text
    assert "Логотип" not in page.text  # header
    assert "© 2026" not in page.text  # footer
    assert "var a=1" not in page.text  # script


async def test_crawl_takes_title_from_head(result):
    page = next(p for p in result.pages if p.url.endswith("/tarify"))
    assert page.title == "Тарифҳо"


async def test_crawl_is_breadth_first(result):
    """Обход в ширину: сначала весь первый уровень, потом второй.

    Проверяем по порядку страниц в результате — глубина не убывает.
    """
    depths = [p.depth for p in result.pages]
    assert depths == sorted(depths)
    assert depths[0] == 0


async def test_crawl_follows_nav_links(result):
    """Меню выкидывается из текста, но ссылки из него брать обязаны —
    иначе краулер увидит одну страницу и остановится."""
    assert "/about" in paths(result)


# ---------------------------------------------------------------------------
# лимиты ТЗ
# ---------------------------------------------------------------------------


async def test_robots_disallow_is_respected(site, result):
    assert "/secret" not in paths(result)
    assert result.stats.skipped_robots == 1
    assert site.hits["/secret"] == 0  # даже не постучались


async def test_missing_robots_allows_everything(site):
    routes = build_routes()
    del routes["/robots.txt"]
    server = FixtureSite(routes).start()
    try:
        result = await crawl(server.base_url, delay=0)
        assert "/secret" in paths(result)
        assert result.stats.skipped_robots == 0
    finally:
        server.stop()


async def test_max_depth_three(result):
    """Глубина 3 = три уровня ссылок от старта; /too-deep лежит на четвёртом."""
    assert "/tarify/details/deep" in paths(result)
    assert "/too-deep" not in paths(result)
    assert max(p.depth for p in result.pages) == 3
    assert result.stats.skipped_depth >= 1


async def test_max_pages_stops_crawl(site):
    result = await crawl(site.base_url, max_pages=2, delay=0)
    assert len(result.pages) == 2
    assert result.stats.stopped_reason == "max_pages"


async def test_full_crawl_reports_empty_queue(result):
    assert result.stats.stopped_reason == "queue_empty"


async def test_max_pages_counts_requests_not_indexed_pages():
    """Лимит про нагрузку на чужой сервер, а не про размер нашей базы.

    Дефект, найденный при перечитывании кода: лимит сравнивался с числом
    проиндексированных страниц. Сайт, где половина страниц пустая, получал
    от нас вдвое больше запросов, чем разрешено, — и молча.
    """
    empty = "<html><head><title>Пусто</title></head><body><nav>меню</nav></body></html>"
    # стартовая страница собрана вручную, без служебной обвязки html():
    # лишние ссылки в меню сбили бы счёт запросов
    start = (
        "<html><head><title>Асосӣ</title></head><body><main>"
        "<p>Рӯйхат</p>"
        "<a href='/e1'>1</a><a href='/e2'>2</a>"
        "<a href='/e3'>3</a><a href='/e4'>4</a>"
        "</main></body></html>"
    ).encode("utf-8")
    routes = {
        "/": Route(body=start),
        **{f"/e{i}": Route(body=empty.encode("utf-8")) for i in range(1, 5)},
    }
    server = FixtureSite(routes).start()
    try:
        result = await crawl(server.base_url, max_pages=3, delay=0)
        requests = sum(v for k, v in server.hits.items() if k != "/robots.txt")

        assert requests == 3
        assert result.stats.fetched == 3
        assert result.stats.stopped_reason == "max_pages"
        # в базу при этом легла одна страница из трёх — и это нормально
        assert len(result.pages) == 1
    finally:
        server.stop()


async def test_delay_between_requests(site):
    """Задержка 0,5 сек в ТЗ — здесь 0,2, чтобы тест не тянулся.

    Проверяем именно паузу МЕЖДУ запросами: N страниц → не меньше (N-1) пауз.
    """
    started = time.monotonic()
    result = await crawl(site.base_url, delay=0.2, max_pages=4)
    elapsed = time.monotonic() - started

    # считаем запросы, а не проиндексированные страницы: часть страниц
    # полигона пустая, в базу они не идут, но сервер дёргают
    assert result.stats.fetched == 4
    # 4 запроса: пауз минимум 3
    assert elapsed >= 0.6


async def test_default_limits_match_tz():
    """Числа из ТЗ не должны уехать при рефакторинге."""
    assert crawler.MAX_PAGES == 150
    assert crawler.MAX_DEPTH == 3
    assert crawler.DELAY_SEC == 0.5


# ---------------------------------------------------------------------------
# решения исполнителя
# ---------------------------------------------------------------------------


async def test_duplicate_urls_fetched_once(site, result):
    """/tarify, /tarify/, /tarify?utm_source=fb и /tarify#rates — одна страница."""
    assert site.hits["/tarify"] == 1
    assert len([p for p in result.pages if p.url.endswith("/tarify")]) == 1
    assert result.stats.skipped_dup >= 3


async def test_offdomain_links_skipped(site, result):
    assert not any("example.org" in p.url for p in result.pages)
    assert result.stats.skipped_offdomain >= 1


async def test_offdomain_redirect_is_dropped(site):
    """Редирект наружу обрываем: source_url обязан остаться в домене банка."""
    other = FixtureSite({"/landing": Route(body=html("<p>Чужой сайт</p>"))}).start()
    try:
        routes = build_routes()
        routes["/"] = Route(
            body=html("<a href='/out'>Наружу</a>", title="Асосӣ")
        )
        routes["/out"] = Route(status=302, location=other.url("/landing"))
        server = FixtureSite(routes).start()
        try:
            result = await crawl(server.base_url, delay=0)
            assert not any("Чужой сайт" in p.text for p in result.pages)
            assert result.stats.skipped_offdomain >= 1
        finally:
            server.stop()
    finally:
        other.stop()


async def test_start_url_may_move_to_another_domain():
    """Стартовый URL имеет право переехать — найдено боевым прогоном.

    eskhata.tj отвечает редиректом на eskhata.com. По общему правилу
    «редирект наружу обрываем» краулер умирал на первом же запросе и
    возвращал ноль страниц.
    """
    new_home = FixtureSite(
        {
            "/": Route(body=html("<a href='/tarify'>Тарифҳо</a>", title="Нав")),
            "/tarify": Route(body=html("<p>Фоизи солона — 14,5%.</p>")),
        }
    ).start()
    old_home = FixtureSite({"/": Route(status=301, location=new_home.url("/"))}).start()
    try:
        result = await crawl(old_home.base_url, delay=0)
        assert len(result.pages) == 2
        assert all(p.url.startswith(new_home.base_url) for p in result.pages)
        assert any("переехал" in line for line in result.log)
    finally:
        old_home.stop()
        new_home.stop()


async def test_inner_page_redirect_outside_still_dropped():
    """Переезд разрешён только стартовой странице, не внутренним."""
    outside = FixtureSite({"/x": Route(body=html("<p>Чужое</p>"))}).start()
    home = FixtureSite(
        {
            "/": Route(body=html("<a href='/out'>Наружу</a>")),
            "/out": Route(status=302, location=outside.url("/x")),
        }
    ).start()
    try:
        result = await crawl(home.base_url, delay=0)
        assert not any("Чужое" in p.text for p in result.pages)
        assert result.stats.skipped_offdomain >= 1
    finally:
        home.stop()
        outside.stop()


async def test_internal_redirect_is_followed(site):
    routes = build_routes()
    routes["/"] = Route(body=html("<a href='/moved'>Переехало</a>"))
    routes["/moved"] = Route(status=301, location="/about")
    server = FixtureSite(routes).start()
    try:
        result = await crawl(server.base_url, delay=0)
        assert "/about" in paths(result)
        # индексируем под конечным URL, а не под тем, по которому пришли
        assert not any(p.url.endswith("/moved") for p in result.pages)
    finally:
        server.stop()


async def test_redirect_target_not_fetched_twice():
    """Конечный адрес редиректа запоминается.

    Дефект, найденный при перечитывании кода: /moved → /about индексировался
    как /about, но сам /about в «увиденные» не попадал. Прямая ссылка на
    него приводила краулер туда второй раз — лишний запрос и дубль
    страницы в базе знаний.
    """
    # стартовая страница собрана вручную: важен порядок ссылок — редирект
    # должен быть разобран РАНЬШЕ прямой ссылки на ту же страницу
    start = (
        "<html><head><title>Асосӣ</title></head><body><main>"
        "<a href='/moved'>Через редирект</a>"
        "<a href='/about'>Напрямую</a>"
        "</main></body></html>"
    ).encode("utf-8")
    routes = {
        "/": Route(body=start),
        "/moved": Route(status=302, location="/about"),
        "/about": Route(body=html("<p>Бонк аз соли 1994.</p>", title="О банке")),
    }
    server = FixtureSite(routes).start()
    try:
        result = await crawl(server.base_url, delay=0)
        # один заход — тот, куда нас привёл редирект; за прямой ссылкой
        # краулер уже не пошёл
        assert server.hits["/about"] == 1
        assert len([p for p in result.pages if p.url.endswith("/about")]) == 1
    finally:
        server.stop()


async def test_asset_listed_once_from_both_paths():
    """Ассет находится двумя путями — по расширению и по Content-Type.

    Раньше вторая ветка складывала в список без проверки на дубль, и один
    файл попадал в отчёт дважды.
    """
    routes = {
        "/": Route(body=html("<a href='/report'>Отчёт</a><a href='/p2'>Ещё</a>")),
        "/p2": Route(body=html("<a href='/report'>Тот же отчёт</a>")),
        "/report": Route(body=b"%PDF-1.4", content_type="application/pdf"),
    }
    server = FixtureSite(routes).start()
    try:
        result = await crawl(server.base_url, delay=0)
        assert [u for u in result.assets if u.endswith("/report")] == [
            server.url("/report")
        ]
    finally:
        server.stop()


async def test_same_text_under_different_urls_indexed_once():
    """Одна страница под двумя адресами — один документ в базе.

    Найдено нагрузочным прогоном: `/tarify` и `/tarify?sort=asc` для
    краулера разные адреса, а текст у них слово в слово один. Без проверки
    содержимого в базу знаний ложатся дубли, а дубли фрагментов перекашивают
    выдачу поиска: один и тот же абзац занимает два места из трёх в топе.
    """
    same = html("<p>Фоизи солона — 14,5%.</p>", title="Тарифҳо")
    routes = {
        "/": Route(
            body=html(
                "<a href='/tarify'>Раз</a><a href='/tarify?sort=asc'>Два</a>"
            )
        ),
        # фикстурный сервер отвечает по пути, query игнорирует —
        # ровно так ведёт себя сайт с необязательным параметром
        "/tarify": Route(body=same),
    }
    server = FixtureSite(routes).start()
    try:
        result = await crawl(server.base_url, delay=0)

        tarify = [p for p in result.pages if "/tarify" in p.url]
        assert len(tarify) == 1
        assert result.stats.skipped_dup_text == 1
        # запрос всё равно был: понять, что текст тот же, можно только скачав
        assert server.hits["/tarify"] == 2
    finally:
        server.stop()


async def test_content_dedup_does_not_lose_links():
    """Копия, найденная выше по дереву, ссылки отдать обязана.

    Иначе дедупликация по тексту молча срезает охват обхода: страница
    встретилась на глубине 3, её копия — на глубине 1, и всё поддерево
    ниже копии осталось несобранным.
    """
    deep = html("<a href='/target'>Цель</a>", title="Развилка")
    routes = {
        "/": Route(body=html("<a href='/a'>A</a><a href='/twin'>Копия</a>")),
        "/a": Route(body=html("<a href='/b'>B</a>")),
        "/b": Route(body=html("<a href='/fork'>Развилка</a>")),
        "/fork": Route(body=deep),   # глубина 3 — ссылки отсюда не берутся
        "/twin": Route(body=deep),   # тот же текст, но глубина 1
        "/target": Route(body=html("<p>Маблағи ҳадди ақал — 500 сомонӣ.</p>")),
    }
    server = FixtureSite(routes).start()
    try:
        result = await crawl(server.base_url, delay=0)
        assert any(p.url.endswith("/target") for p in result.pages)
        assert result.stats.skipped_dup_text == 1
    finally:
        server.stop()


async def test_site_requiring_trailing_slash():
    """Сайт с APPEND_SLASH: без слэша 404, со слэшем 200.

    Так устроены Битрикс и Django — то есть половина сайтов банков РТ.
    Краулер обязан ходить по адресу, который написан на странице, а не по
    своему нормализованному ключу.
    """
    # стартовая страница без служебного меню: лишние ссылки дали бы свои
    # 404 и смазали проверку
    start = (
        "<html><head><title>Асосӣ</title></head><body><main>"
        "<a href='/tarify/'>Тарифҳо</a></main></body></html>"
    ).encode("utf-8")
    page = (
        "<html><head><title>Тарифҳо</title></head><body><main>"
        "<p>Фоизи солона — 14,5%.</p></main></body></html>"
    ).encode("utf-8")
    routes = {
        "/": Route(body=start),
        "/tarify/": Route(body=page),
        # без слэша сайт отвечает 404 — как настоящий Битрикс
        "/tarify": Route(status=404, body=b"not found", content_type="text/plain"),
    }
    server = FixtureSite(routes).start()
    try:
        result = await crawl(server.base_url, delay=0)
        assert any(p.url.endswith("/tarify/") for p in result.pages)
        assert result.stats.errors == 0
    finally:
        server.stop()


async def test_start_url_without_slash_is_retried_with_slash():
    """Человек напечатал стартовый адрес без слэша — не повод падать."""
    routes = {
        "/docs/": Route(body=html("<p>Ҳуҷҷатҳо.</p>", title="Документы")),
        "/docs": Route(status=404, body=b"not found", content_type="text/plain"),
    }
    server = FixtureSite(routes).start()
    try:
        result = await crawl(server.url("/docs"), delay=0)
        assert len(result.pages) == 1
        assert result.pages[0].url.endswith("/docs/")
        assert any("пробую со слэшем" in line for line in result.log)
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# языковые версии сайта
# ---------------------------------------------------------------------------


def test_language_variants_from_select():
    """Переключатель языков у Битрикса — <select>, а не ссылка.

    Именно так устроена Эсхата: таджикская версия на tj.eskhata.com
    указана в `option value`, и по `a[href]` её не найти никогда.
    """
    html = (
        "<html><body><select name='language'>"
        "<option disabled>Русский</option>"
        "<option value=''>Русский</option>"
        "<option value='https://tj.bank.tj/'> Тоҷикӣ</option>"
        "</select></body></html>"
    )
    assert language_variants(html, "https://bank.tj/") == ["https://tj.bank.tj/"]


def test_language_variants_from_hreflang():
    """Стандартный способ. У Эсхаты его нет, у следующего банка может быть."""
    html = (
        "<html><head>"
        "<link rel='alternate' hreflang='tg' href='/tj/'>"
        "<link rel='alternate' hreflang='en' href='https://en.bank.tj/'>"
        "</head><body></body></html>"
    )
    assert language_variants(html, "https://bank.tj/") == [
        "https://bank.tj/tj/",
        "https://en.bank.tj/",
    ]


def test_language_variants_ignores_current_language():
    """`option value=''` — это текущий язык, не другая версия."""
    html = "<select name='language'><option value=''>Русский</option></select>"
    assert language_variants(html, "https://bank.tj/") == []


async def test_crawler_follows_language_variant():
    """Обе версии обходятся одним заданием.

    Без этого база знаний остаётся одноязычной: таджикские вопросы ищут
    по русским документам и теряют около 0,19 близости.
    """
    ru = FixtureSite(
        {
            "/": Route(
                body=html(
                    "<h1>Тарифы</h1><p>Ставка 14,5%.</p>", title="Главная"
                )
            )
        }
    ).start()
    try:
        tj = FixtureSite(
            {
                "/": Route(
                    body=html("<h1>Тарифҳо</h1><p>Фоиз 14,5%.</p>", title="Асосӣ")
                )
            }
        ).start()
        try:
            # переключатель языков на русской версии ведёт на таджикскую
            ru.routes["/"] = Route(
                body=html(
                    "<h1>Тарифы</h1><p>Ставка 14,5%.</p>"
                    f"<select name='language'><option value=''>Русский</option>"
                    f"<option value='{tj.base_url}/'>Тоҷикӣ</option></select>",
                    title="Главная",
                )
            )
            result = await crawl(ru.base_url, delay=0)

            hosts = {urlsplit(page.url).netloc for page in result.pages}
            assert len(hosts) == 2, f"обошли только {hosts}"
            assert result.stats.language_variants == 1
            assert any("Фоиз" in page.text for page in result.pages)
        finally:
            tj.stop()
    finally:
        ru.stop()


async def test_language_variants_can_be_disabled(site):
    """Выключатель возвращает прежнее поведение — один сайт, один язык."""
    result = await crawl(site.base_url, delay=0, follow_language_variants=False)
    assert result.stats.language_variants == 0


def test_same_domain_allows_only_listed_hosts():
    """Поддомен становится своим, только если он найден как языковая
    версия. Иначе `blog.bank.tj` утащил бы обход в чужой раздел."""
    assert same_domain("https://tj.bank.tj/x", "https://bank.tj/", {"tj.bank.tj"})
    assert not same_domain("https://blog.bank.tj/x", "https://bank.tj/", {"tj.bank.tj"})
    assert not same_domain("https://tj.bank.tj/x", "https://bank.tj/", set())


# ---------------------------------------------------------------------------
# пагинация и исключённые разделы
# ---------------------------------------------------------------------------


async def test_pagination_param_is_dropped():
    """`?PAGEN_1=3` схлопывается в сам адрес — второго запроса нет.

    Битрикс нумерует каждый блок списка своим параметром, и они
    комбинируются: боевой обход eskhata.com дал 27 адресов вида
    `?PAGEN_1=2&PAGEN_3=4`. Canonical сайт по ним не отдаёт (проверено
    запросом), поэтому единственная защита — выбросить сам параметр.
    """
    assert normalize_url("https://b.tj/kursy?PAGEN_1=3") == "https://b.tj/kursy"
    assert (
        normalize_url("https://b.tj/events/?PAGEN_1=2&PAGEN_3=4")
        == "https://b.tj/events"
    )
    # общие имена тоже: page=2 это та же листалка
    assert normalize_url("https://b.tj/list?page=2") == "https://b.tj/list"


async def test_meaningful_param_survives_pagination_filter():
    """Сторож от жадности: `lang` — не пагинация и обязан остаться.

    Без этого теста легко «почистить» заодно язык страницы, и таджикская
    версия сайта в базу знаний не попадёт.
    """
    assert normalize_url("https://b.tj/about?lang=tj") == "https://b.tj/about?lang=tj"
    # и параметр, лишь похожий на пагинацию началом имени
    assert (
        normalize_url("https://b.tj/x?pagenumber_hint=1")
        == "https://b.tj/x?pagenumber_hint=1"
    )


async def test_paginated_url_is_not_requested_twice(site, result):
    """Страница списка скачивается один раз, а не по разу на страницу.

    На полигоне со стартовой ведут `/kursy` и `/kursy?PAGEN_1=3` — сервер
    отвечает по пути и на то, и на другое, так что без фильтра было бы
    два обращения.
    """
    assert site.hits["/kursy"] == 1


async def test_excluded_sections_are_not_crawled(site, result):
    """Новости и вакансии не попадают в базу знаний.

    Со стартовой на полигоне ведут четыре ссылки в исключённые разделы,
    включая вложенную `/events/pro-vklad/`. Ни одна не должна быть даже
    запрошена: раздел отсекается до постановки в очередь.
    """
    assert "/events/" not in paths(result)
    assert "/events/pro-vklad/" not in paths(result)
    assert "/vacancies/" not in paths(result)
    assert site.hits["/events/"] == 0
    assert site.hits["/vacancies/"] == 0
    assert result.stats.skipped_section > 0


async def test_skip_paths_can_be_disabled(site):
    """Список разделов — параметр, а не приговор.

    У другого банка тарифы могут лежать в /news/, поэтому отсев должен
    выключаться снаружи без правки кода.
    """
    result = await crawl(site.base_url, delay=0, skip_paths=())
    assert "/events/" in paths(result)
    assert result.stats.skipped_section == 0


def test_is_skipped_path_needs_full_segment():
    """`/news/` не должен убивать `/newsletter/` — сравнение по сегменту."""
    prefixes = ("/news/", "/events/")
    assert is_skipped_path("https://b.tj/news/", prefixes)
    assert is_skipped_path("https://b.tj/news", prefixes)  # без слэша
    assert is_skipped_path("https://b.tj/events/pro-vklad/", prefixes)
    assert not is_skipped_path("https://b.tj/newsletter/", prefixes)
    assert not is_skipped_path("https://b.tj/tarify/", prefixes)


async def test_canonical_collapses_pagination():
    """<link rel="canonical"> схлопывает пагинацию в одну страницу.

    Проверяется именно механизм canonical, поэтому отсев разделов здесь
    выключен (`skip_paths=()`): у настоящей Эсхаты `/events/` в базу знаний
    не идёт вовсе, а вот у другого банка тарифы вполне могут лежать в
    разделе с пагинацией, и тогда работать должен canonical.
    """
    def page(canon: str, body: str) -> bytes:
        return (
            f"<html><head><title>События</title>"
            f"<link rel='canonical' href='{canon}'></head>"
            f"<body><main>{body}</main></body></html>"
        ).encode("utf-8")

    # Параметры здесь НЕ пагинационные: `?PAGEN_1=2` фильтр выбрасывает
    # сам, и canonical проверять было бы нечем. Берём сортировку и фильтр
    # списка — их мы не трогаем, а разными адресами одной страницы они
    # быть не перестают.
    routes = {
        "/": Route(
            body=(
                "<html><head><title>Асосӣ</title></head><body><main>"
                "<a href='/events/'>Хабарҳо</a>"
                "<a href='/events/?sort=date'>Аз рӯи сана</a>"
                "<a href='/events/?tag=vklad'>Танҳо амонатҳо</a>"
                "</main></body></html>"
            ).encode("utf-8")
        ),
        "/events/": Route(body=page("/events/", "<p>Хабарҳои бонк.</p>")),
    }
    server = FixtureSite(routes).start()
    try:
        result = await crawl(server.base_url, delay=0, skip_paths=())
        events = [p for p in result.pages if "/events/" in p.url]
        assert len(events) == 1
        assert events[0].url.endswith("/events/")
        assert any("canonical" in line for line in result.log)
    finally:
        server.stop()


async def test_meta_robots_noindex_is_respected():
    """noindex — та же просьба, что и в robots.txt, только в теге."""
    routes = {
        "/": Route(
            body=(
                "<html><head><title>Асосӣ</title></head><body><main>"
                "<a href='/draft/'>Черновик</a></main></body></html>"
            ).encode("utf-8")
        ),
        "/draft/": Route(
            body=(
                "<html><head><title>Черновик</title>"
                "<meta name='robots' content='noindex, follow'></head>"
                "<body><main><p>Не для индекса.</p></main></body></html>"
            ).encode("utf-8")
        ),
    }
    server = FixtureSite(routes).start()
    try:
        result = await crawl(server.base_url, delay=0)
        assert not any("Не для индекса" in p.text for p in result.pages)
        assert result.stats.skipped_robots == 1
    finally:
        server.stop()


async def test_rel_nofollow_link_not_followed(site):
    routes = {
        "/": Route(
            body=(
                "<html><head><title>Асосӣ</title></head><body><main>"
                "<a href='/ok/'>Можно</a>"
                "<a href='/skip/' rel='nofollow'>Нельзя</a>"
                "</main></body></html>"
            ).encode("utf-8")
        ),
        "/ok/": Route(body=html("<p>Мумкин.</p>")),
        "/skip/": Route(body=html("<p>Намумкин.</p>")),
    }
    server = FixtureSite(routes).start()
    try:
        result = await crawl(server.base_url, delay=0)
        assert server.hits["/skip/"] == 0
        assert any(p.url.endswith("/ok/") for p in result.pages)
    finally:
        server.stop()


async def test_429_is_waited_out(monkeypatch):
    """429 — просьба притормозить, а не отказ."""
    monkeypatch.setattr(crawler, "RETRY_PAUSE_SEC", 0)
    routes = {
        "/": Route(
            body=(
                "<html><head><title>Асосӣ</title></head><body><main>"
                "<a href='/busy/'>Занято</a></main></body></html>"
            ).encode("utf-8")
        ),
        "/busy/": Route(body=html("<p>Дастрас шуд.</p>"), fail_times=1, fail_status=429),
    }
    server = FixtureSite(routes).start()
    try:
        result = await crawl(server.base_url, delay=0)
        assert any("Дастрас шуд" in p.text for p in result.pages)
        assert server.hits["/busy/"] == 2
    finally:
        server.stop()


async def test_oversized_page_skipped():
    """Пятимегабайтная «страница» — это выгрузка, а не документ."""
    big = (
        "<html><head><title>Дамп</title></head><body><main><p>"
        + "а" * (crawler.MAX_BYTES + 1000)
        + "</p></main></body></html>"
    ).encode("utf-8")
    routes = {
        "/": Route(
            body=(
                "<html><head><title>Асосӣ</title></head><body><main>"
                "<a href='/dump/'>Дамп</a></main></body></html>"
            ).encode("utf-8")
        ),
        "/dump/": Route(body=big),
    }
    server = FixtureSite(routes).start()
    try:
        result = await crawl(server.base_url, delay=0)
        assert not any(p.url.endswith("/dump/") for p in result.pages)
        assert any("слишком большая" in line for line in result.log)
    finally:
        server.stop()


@pytest.mark.parametrize(
    "raw, expected",
    [
        # сессионные параметры плодят по новому URL на каждый заход
        ("https://bank.tj/a?PHPSESSID=abc", "https://bank.tj/a"),
        ("https://bank.tj/a?jsessionid=1&lang=tj", "https://bank.tj/a?lang=tj"),
        # а похожий по названию, но значимый параметр остаётся
        ("https://bank.tj/a?_gap=1", "https://bank.tj/a?_gap=1"),
        # регистр процентного кодирования незначим
        ("https://bank.tj/%d0%b0", "https://bank.tj/%D0%B0"),
    ],
)
def test_url_cleanup_edge_cases(raw, expected):
    assert normalize_url(raw) == expected


async def test_base_href_is_respected():
    """<base href> переопределяет базу для относительных ссылок."""
    routes = {
        "/": Route(
            body=(
                "<html><head><title>Асосӣ</title><base href='/docs/'></head>"
                "<body><main><a href='tarify'>Тарифҳо</a></main></body></html>"
            ).encode("utf-8")
        ),
        "/docs/tarify": Route(body=html("<p>Фоизи солона — 14,5%.</p>")),
    }
    server = FixtureSite(routes).start()
    try:
        result = await crawl(server.base_url, delay=0)
        assert any(p.url.endswith("/docs/tarify") for p in result.pages)
    finally:
        server.stop()


async def test_pdf_links_collected_not_downloaded(site, result):
    """PDF на сайте не качаем (это зона ingest), но список отдаём человеку."""
    assert any(url.endswith("/files/tarify.pdf") for url in result.assets)
    assert not any(p.url.endswith(".pdf") for p in result.pages)


async def test_non_html_content_type_skipped(site):
    """Ссылка без расширения, но с чужим Content-Type — тоже мимо."""
    routes = build_routes()
    routes["/"] = Route(body=html("<a href='/report'>Отчёт</a>"))
    routes["/report"] = Route(body=b"%PDF-1.4", content_type="application/pdf")
    server = FixtureSite(routes).start()
    try:
        result = await crawl(server.base_url, delay=0)
        assert not any(p.url.endswith("/report") for p in result.pages)
        assert result.stats.skipped_asset >= 1
        assert any(url.endswith("/report") for url in result.assets)
    finally:
        server.stop()


async def test_windows1251_page_decoded(result):
    """Сервер не прислал кодировку, она только в <meta>.

    Без разбора meta таджикский и русский текст превращается в мусор, и это
    не падает, а тихо портит базу знаний.
    """
    page = next(p for p in result.pages if p.url.endswith("/kursy"))
    assert "Курс доллара — 10,90 сомони" in page.text


async def test_empty_page_not_indexed(site, result):
    """Страница из одних служебных тегов: скачали, но чанков из неё нет."""
    assert "/empty" not in paths(result)
    assert site.hits["/empty"] == 1
    assert result.stats.fetched > result.stats.indexed


async def test_broken_link_counted_as_error(result):
    assert result.stats.errors >= 1
    assert "/gone" not in paths(result)


async def test_500_is_retried(monkeypatch):
    """Два 500 подряд — не повод терять страницу: ретраим (решение 4)."""
    monkeypatch.setattr(crawler, "RETRY_PAUSE_SEC", 0)
    routes = {
        "/": Route(body=html("<a href='/flaky'>Тест</a>")),
        "/flaky": Route(body=html("<p>Ответ после сбоя</p>"), fail_times=2),
    }
    server = FixtureSite(routes).start()
    try:
        result = await crawl(server.base_url, delay=0)
        assert any("Ответ после сбоя" in p.text for p in result.pages)
        assert server.hits["/flaky"] == 3
    finally:
        server.stop()


async def test_404_is_not_retried(monkeypatch, site):
    """4xx — это ответ, а не сбой: повторять бессмысленно и невежливо."""
    monkeypatch.setattr(crawler, "RETRY_PAUSE_SEC", 0)
    await crawl(site.base_url, delay=0)
    assert site.hits["/gone"] == 1


async def test_title_falls_back_to_url_tail():
    routes = {
        "/": Route(
            body="<html><body><main><p>Матн бе сарлавҳа</p></main></body></html>".encode(
                "utf-8"
            )
        )
    }
    server = FixtureSite(routes).start()
    try:
        result = await crawl(server.base_url, delay=0)
        assert result.pages[0].title  # пустым не оставляем
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# лог сдачи
# ---------------------------------------------------------------------------


async def test_javascript_site_is_reported_not_silently_empty():
    """Сайт на JS даёт пустую базу — краулер обязан сказать об этом вслух.

    Найдено прогоном по humo.tj: главная отдаёт 33 символа текста при 42
    тегах <script>. Молча вернуть ноль страниц нельзя: документ получит
    статус «Проиндексирован», на экране будет зелёная галочка, а бот
    начнёт эскалировать вообще всё.
    """
    shell = (
        "<html><head><title>Бонк</title></head><body>"
        "<div id='app'></div><script>render();</script>"
        "<main></main>"
        "{links}</body></html>"
    )
    routes = {
        "/": Route(
            body=shell.format(
                links="<a href='/p1/'>1</a><a href='/p2/'>2</a><a href='/p3/'>3</a>"
            ).encode("utf-8")
        ),
        **{
            f"/p{i}/": Route(body=shell.format(links="").encode("utf-8"))
            for i in range(1, 4)
        },
    }
    server = FixtureSite(routes).start()
    try:
        result = await crawl(server.base_url, delay=0)
        assert result.stats.fetched == 4
        assert any("JavaScript" in line for line in result.log)
    finally:
        server.stop()


async def test_normal_site_gets_no_javascript_warning(result):
    """Обратная сторона: на обычном сайте предупреждения быть не должно."""
    assert not any("JavaScript" in line for line in result.log)


async def test_report_has_everything_for_demo(result):
    """Критерий сдачи требует показать лог: сколько страниц, какая глубина,
    где остановился, что отсёк robots.txt."""
    report = format_report(result)
    for line in (
        "успешных запросов",
        "страниц с текстом",
        "максимальная глубина",
        "отсечено robots.txt",
        "чужой домен",
        "остановка",
    ):
        assert line in report
    assert "[robots]" in report

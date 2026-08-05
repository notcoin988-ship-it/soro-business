"""Мини-сайт на локальном HTTP-сервере — полигон для краулера.

Зачем не боевой eskhata.tj: тесты не должны зависеть от сети и от чужой
вёрстки. Здесь ловушки собраны специально — циклические ссылки, дубли URL,
редирект на другой домен, robots.txt, PDF, страница в windows-1251,
нестабильный ответ 500 и глубина больше трёх. Живой прогон по сайту банка
делается отдельно, скриптом scripts/crawl_report.py.

Сервер поднимается на случайном порту в отдельном потоке и считает
обращения: `site.hits['/tarify']` — сколько раз краулер сходил на страницу.
Это единственный способ проверить дедупликацию честно, а не по логу.
"""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class Route:
    body: bytes = b""
    status: int = 200
    content_type: str = "text/html; charset=utf-8"
    location: str | None = None
    # сколько первых обращений отдать ошибкой (проверка ретраев)
    fail_times: int = 0
    fail_status: int = 500  # 500 — сбой, 429 — просьба притормозить
    failed: int = field(default=0, init=False)


def html(body: str, title: str = "Бонк") -> bytes:
    """Страница с полным набором служебных тегов — их парсер обязан снять."""
    return (
        f"<html><head><title>{title}</title></head><body>"
        f"<header>Логотип</header>"
        f"<nav><a href='/'>Асосӣ</a><a href='/about'>Дар бораи мо</a></nav>"
        f"<main>{body}</main>"
        f"<footer>© 2026</footer>"
        f"<script>var a=1;</script>"
        f"</body></html>"
    ).encode("utf-8")


class FixtureSite:
    def __init__(self, routes: dict[str, Route]):
        self.routes = routes
        self.hits: Counter[str] = Counter()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address[:2]
        return f"http://127.0.0.1:{port}"

    def url(self, path: str) -> str:
        return self.base_url + path

    def start(self) -> "FixtureSite":
        site = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):  # noqa: N802 — имя задано stdlib
                path = self.path.split("?", 1)[0]
                site.hits[path] += 1
                route = site.routes.get(path)

                if route is None:
                    self._send(404, b"not found", "text/plain; charset=utf-8")
                    return

                if route.failed < route.fail_times:
                    route.failed += 1
                    self._send(
                        route.fail_status,
                        b"try later",
                        "text/plain; charset=utf-8",
                    )
                    return

                if route.location is not None:
                    self.send_response(route.status or 302)
                    self.send_header("Location", route.location)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                self._send(route.status, route.body, route.content_type)

            def _send(self, status: int, body: bytes, content_type: str):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # тишина в выводе pytest
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


# ---------------------------------------------------------------------------
# основной полигон
# ---------------------------------------------------------------------------

ROBOTS = b"User-agent: *\nDisallow: /secret\n"

WIN1251_PAGE = (
    "<html><head><meta charset='windows-1251'><title>Курсы</title></head>"
    "<body><main><p>Курс доллара — 10,90 сомони</p></main></body></html>"
).encode("windows-1251")


def build_routes() -> dict[str, Route]:
    return {
        "/robots.txt": Route(body=ROBOTS, content_type="text/plain; charset=utf-8"),
        # стартовая: дубли одного URL в трёх видах, чужой домен, PDF, mailto
        "/": Route(
            body=html(
                "<h1>Асосӣ</h1><p>Хуш омадед ба бонк.</p>"
                "<a href='/tarify'>Тарифҳо</a>"
                "<a href='/tarify/'>Тарифҳо со слэшем</a>"
                "<a href='/tarify?utm_source=fb'>Тарифҳо из рекламы</a>"
                "<a href='/tarify#rates'>Тарифҳо с якорем</a>"
                "<a href='/about'>Дар бораи мо</a>"
                "<a href='/secret'>Служебное</a>"
                "<a href='/files/tarify.pdf'>Тарифы PDF</a>"
                "<a href='/empty'>Пустая</a>"
                "<a href='/kursy'>Курсы</a>"
                "<a href='/gone'>Битая</a>"
                "<a href='https://example.org/x'>Другой сайт</a>"
                "<a href='mailto:info@bank.tj'>Почта</a>"
                "<a href='#top'>Наверх</a>",
                title="Бонки Эсхата — асосӣ",
            )
        ),
        "/tarify": Route(
            body=html(
                "<h1>Тарифҳо</h1><p>Фоизи солона — 14,5%.</p>"
                "<a href='/tarify/details'>Тафсилот</a>",
                title="Тарифҳо",
            )
        ),
        "/about": Route(body=html("<p>Бонк аз соли 1994.</p>", title="Дар бораи мо")),
        "/tarify/details": Route(
            body=html(
                "<p>Маблағи ҳадди ақал — 500 сомонӣ.</p>"
                "<a href='/tarify/details/deep'>Боз</a>",
                title="Тафсилот",
            )
        ),
        # глубина 3 — со страницы ссылки уже не берём
        "/tarify/details/deep": Route(
            body=html(
                "<p>Замима.</p><a href='/too-deep'>Глубже</a>", title="Замима"
            )
        ),
        "/too-deep": Route(body=html("<p>Сюда краулер дойти не должен.</p>")),
        "/secret": Route(body=html("<p>Служебная страница.</p>")),
        "/files/tarify.pdf": Route(body=b"%PDF-1.4 fake", content_type="application/pdf"),
        # только служебные теги: текста после очистки не остаётся
        "/empty": Route(
            body=(
                "<html><head><title>Пусто</title></head><body>"
                "<nav>меню</nav><footer>подвал</footer></body></html>"
            ).encode("utf-8")
        ),
        # сервер не сказал кодировку — она только в <meta>
        "/kursy": Route(body=WIN1251_PAGE, content_type="text/html"),
    }

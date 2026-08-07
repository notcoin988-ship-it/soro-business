"""Переранжирование кандидатов кросс-энкодером (`core/rag.rerank`).

Боевой переранкер живёт в своём контейнере, качает 2 ГБ весов и считает
пару около секунды на CPU. Тесты ядра от него не зависят — он выключен
общей фикстурой в conftest. Здесь он включается обратно, но вместо
настоящего TEI поднимается подставной сервер: проверяется наш разбор
ответа и, главное, поведение при отказе.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.config import settings
from app.core.rag import Hit, rerank


class FakeReranker:
    """Сервер, отвечающий как TEI `/rerank`.

    `order` — индексы кандидатов в порядке убывания оценки; оценки
    выдаются по убыванию от 0,9. `status` != 200 изображает падение.
    """

    def __init__(self, order: list[int] | None = None, *, status: int = 200):
        self.order = order
        self.status = status
        self.requests: list[dict] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> FakeReranker:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append(payload)

                if outer.status != 200:
                    self.send_response(outer.status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                count = len(payload.get("texts", []))
                order = outer.order or list(range(count))
                body = json.dumps(
                    [
                        {"index": index, "score": round(0.9 - position * 0.3, 3)}
                        for position, index in enumerate(order)
                    ]
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(timeout=5)


def hit(chunk_id: int, text: str, score: float = 0.5) -> Hit:
    return Hit(
        chunk_id=chunk_id,
        document_id=chunk_id * 10,
        title=f"Документ {chunk_id}",
        page=1,
        source_url=None,
        text=text,
        score=score,
        rrf=0.03,
    )


HITS = [
    hit(1, "Вакансии банка", 0.62),
    hit(2, "Фоизи солонаи амонат 14,5% мебошад", 0.55),
    hit(3, "Курсы валют на сегодня", 0.58),
]


@pytest.fixture
def fake(monkeypatch):
    def make(order=None, status=200):
        server = FakeReranker(order, status=status).start()
        monkeypatch.setattr(settings, "RERANKER_URL", server.url, raising=False)
        return server

    servers: list[FakeReranker] = []

    def factory(*args, **kwargs):
        server = make(*args, **kwargs)
        servers.append(server)
        return server

    yield factory
    for server in servers:
        server.stop()


# ---------------------------------------------------------------------------
# порядок и оценки
# ---------------------------------------------------------------------------


async def test_rerank_reorders_by_cross_encoder(fake):
    """Смысл переранкера: поднять наверх то, что гибридный поиск задвинул.

    Косинус поставил «Вакансии» первыми (0,62) — у bge-m3 своя шкала и на
    таджикском она занижена. Кросс-энкодер видит вопрос и фрагмент вместе
    и ставит первым настоящий ответ.
    """
    fake(order=[1, 2, 0])

    ranked = await rerank("фоизи амонат чанд аст", HITS)

    assert [h.chunk_id for h in ranked] == [2, 3, 1]
    assert ranked[0].rerank > ranked[1].rerank > ranked[2].rerank


async def test_cosine_score_is_kept(fake):
    """Косинус остаётся в `score`: он показывается на «Площадке», и
    подменять его чужой шкалой значит врать в «стеклянном ящике»."""
    fake(order=[1, 0, 2])

    ranked = await rerank("вопрос", HITS)
    original = {h.chunk_id: h.score for h in HITS}

    assert all(h.score == original[h.chunk_id] for h in ranked)


async def test_candidates_and_text_are_trimmed(fake, monkeypatch):
    """На CPU кросс-энкодер считает каждую пару целиком: 12 длинных
    фрагментов не укладываются в норматив. Режем и число, и длину."""
    monkeypatch.setattr(settings, "RERANK_CANDIDATES", 2, raising=False)
    monkeypatch.setattr(settings, "RERANK_TEXT_LIMIT", 10, raising=False)
    server = fake()

    await rerank("вопрос", HITS)

    sent = server.requests[0]
    assert len(sent["texts"]) == 2
    assert all(len(t) <= 10 for t in sent["texts"])


async def test_raw_scores_disabled(fake):
    """Просим нормированную оценку 0..1: пороги подобраны по ней, а сырые
    логиты сравнивать с 0,20 бессмысленно."""
    server = fake()
    await rerank("вопрос", HITS)
    assert server.requests[0]["raw_scores"] is False


# ---------------------------------------------------------------------------
# отказы
# ---------------------------------------------------------------------------


async def test_disabled_by_empty_url(monkeypatch):
    """Пустой адрес выключает переранжирование целиком."""
    monkeypatch.setattr(settings, "RERANKER_URL", "", raising=False)
    assert await rerank("вопрос", HITS) is None


async def test_unavailable_reranker_returns_none(fake):
    """Сервис упал — поиск обязан работать дальше, просто хуже.

    Возврат None означает «переранжирования не было», и `search` решает по
    косинусу со старым порогом. Падать из-за необязательного сервиса
    нельзя: клиент ждёт ответа.
    """
    fake(status=503)
    assert await rerank("вопрос", HITS) is None


async def test_empty_hits_short_circuit(fake):
    server = fake()
    assert await rerank("вопрос", []) is None
    assert server.requests == [], "звали переранкер без кандидатов"


async def test_index_out_of_range_ignored(fake):
    """Чужой ответ не должен ронять поиск: индекс вне диапазона пропускаем."""
    fake(order=[0, 99])

    ranked = await rerank("вопрос", HITS)

    assert [h.chunk_id for h in ranked] == [1]

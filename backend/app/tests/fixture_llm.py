"""Подставная модель по OpenAI-совместимому API.

Боевой Soro крутится на чужом сервере, который бывает недоступен (и был
недоступен, когда писался `core/llm.py`). Держать тесты ядра в зависимости
от него нельзя: они должны падать из-за нашего кода, а не из-за чужого
аптайма. Поэтому здесь — свой `/v1/chat/completions`, отдающий SSE ровно
в том формате, что и vLLM.

Проверяется при этом настоящий сетевой путь: httpx, стриминг, разбор
`data: {...}`, `[DONE]`. Мокать сам httpx смысла нет — тогда мы бы
тестировали заглушку.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeSoro:
    """Сервер, отдающий заранее заданный ответ по кускам.

    `reply` режется на куски по словам: так проверяется, что клиент
    склеивает поток, а не полагается на один пакет.
    """

    def __init__(
        self,
        reply: str = "Фоизи солона 14,5% [1].",
        *,
        status: int = 200,
        chunk_size: int = 3,
        condensed: str | None = None,
    ):
        self.reply = reply
        self.status = status
        self.chunk_size = chunk_size
        # Ответ на запрос БЕЗ стрима — так ходит только переписыватель
        # вопроса (`llm.condense_question`). None означает «вернуть
        # реплику как есть»: это штатное поведение по правилу 4 его
        # промпта, и переписывания в таком случае не происходит.
        self.condensed = condensed
        # запросы, которые пришли: тесты смотрят, что именно ушло в модель
        self.requests: list[dict] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        assert self._server is not None, "сервер не запущен"
        host, port = self._server.server_address[:2]
        return f"http://127.0.0.1:{port}/v1"

    def start(self) -> FakeSoro:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # тишина в выводе тестов
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                try:
                    outer.requests.append(json.loads(body))
                except json.JSONDecodeError:
                    outer.requests.append({"raw": body.decode("utf-8", "replace")})

                if outer.status != 200:
                    payload = json.dumps({"error": "нет"}).encode()
                    self.send_response(outer.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return

                request = outer.requests[-1]
                if not request.get("stream"):
                    # Переписывание вопроса: обычный JSON, не SSE. Если
                    # ответ не задан, отдаём последнюю реплику клиента —
                    # правило 4 промпта («понятно и без истории»).
                    text = outer.condensed
                    if text is None:
                        content = request["messages"][-1]["content"]
                        text = content.rsplit(":", 1)[-1].strip()
                    payload = json.dumps(
                        {"choices": [{"message": {"content": text}}]},
                        ensure_ascii=False,
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return

                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()

                words = outer.reply.split(" ")
                for start in range(0, len(words), outer.chunk_size):
                    piece = " ".join(words[start : start + outer.chunk_size])
                    if start + outer.chunk_size < len(words):
                        piece += " "
                    frame = {
                        "choices": [{"delta": {"content": piece}, "index": 0}]
                    }
                    self.wfile.write(
                        f"data: {json.dumps(frame, ensure_ascii=False)}\n\n".encode()
                    )
                    self.wfile.flush()
                # финальный кусок без содержимого — vLLM шлёт именно так
                self.wfile.write(
                    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                )
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

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

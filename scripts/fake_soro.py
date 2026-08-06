"""Заглушка Soro для показа консоли, когда боевой сервер недоступен.

Отдаёт OpenAI-совместимый SSE ровно как vLLM, с паузой между кусками —
чтобы печать ответа была видна глазом. Нужна для экрана 03: без модели
там видно только поиск, а телеметрия генерации остаётся пустой.

ЭТО НЕ ТЕСТОВАЯ ФИКСТУРА. Тесты используют app/tests/fixture_llm.py,
который поднимается сам и не требует ничего запускать руками.

Как включить (docker-compose.override.yml, он локальный):

    services:
      backend:
        environment:
          SORO_API_URL: "http://127.0.0.1:9000/v1"

затем:

    docker compose up -d backend
    docker compose exec -d backend python /code/scripts/fake_soro.py

Выключить — убрать environment и пересоздать backend.
"""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLY = (
    "Фоизи солонаи амонати «Ояндасоз» 14,5% мебошад, маблағи ҳадди ақал — "
    "500 сомонӣ [1]. Мӯҳлат аз 12 то 36 моҳ. Ҷуброни пеш аз мӯҳлат аз рӯи "
    "фоизи «дархостӣ» 0,5% ҳисоб карда мешавад [2]. Шартҳоро дақиқтар фаҳмонам?"
)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()

        words = REPLY.split(" ")
        for start in range(0, len(words), 2):
            piece = " ".join(words[start : start + 2]) + " "
            frame = {"choices": [{"delta": {"content": piece}, "index": 0}]}
            self.wfile.write(
                f"data: {json.dumps(frame, ensure_ascii=False)}\n\n".encode()
            )
            self.wfile.flush()
            time.sleep(0.08)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


ThreadingHTTPServer(("127.0.0.1", 9000), Handler).serve_forever()

"""Сторож стенда: спрашивает /health/ready и жалуется, когда сломалось.

ЗАЧЕМ. Пока сторожа не было, узнать, что бот замолчал, можно было только
открыв консоль. На демо это выясняется в момент, когда клиент банка уже
задал вопрос.

  docker compose exec backend python scripts/watchdog.py            # разово
  docker compose exec -d backend python scripts/watchdog.py --loop  # фоном

КУДА ЖАЛУЕТСЯ. В лог всегда; в Telegram — если задан `WATCHDOG_CHAT_ID`
(свой id можно узнать у @userinfobot). Отдельного канала оповещений в ТЗ
нет, а заводить почтовый сервер ради демо-стенда незачем: бот у нас уже
есть, и сообщение приходит туда же, где сидит команда.

НЕ СПАМИТ. Сообщение уходит на ПЕРЕХОДЕ состояния: сломалось — написал,
починилось — написал «снова работает». Стенд, который лежит ночь, не
должен выдать триста одинаковых сообщений к утру.

КОД ВОЗВРАТА при разовом запуске: 0 — всё хорошо, 1 — что-то сломано.
Годится для cron и для чек-листа перед показом.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

import httpx

sys.path.insert(0, "/code")

from app.config import is_filled, settings  # noqa: E402

BASE = "http://127.0.0.1:8000"

# Как часто спрашивать. Минута: чаще незачем — самое быстрое, что тут
# ломается, это туннель, и он не чинится сам за секунды.
EVERY_SEC = 60

log = logging.getLogger("watchdog")


async def probe(base: str) -> dict:
    async with httpx.AsyncClient(base_url=base, timeout=15) as client:
        response = await client.get("/health/ready")
        return response.json()


async def shout(text: str) -> None:
    """Сказать в Telegram, если есть кому."""
    chat_id = os.environ.get("WATCHDOG_CHAT_ID", "").strip()
    if not chat_id or not is_filled(settings.TELEGRAM_BOT_TOKEN):
        return

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={"chat_id": chat_id, "text": text})
    except Exception as exc:  # noqa: BLE001 — сторож не должен падать сам
        log.warning("не смог отправить оповещение: %s", exc)


def describe(report: dict) -> str:
    broken = report.get("broken") or []
    if not broken:
        return "Стенд Soro: всё работает."

    lines = ["Стенд Soro: сломалось " + ", ".join(broken)]
    for name in broken:
        detail = report["checks"][name].get("detail") or "без подробностей"
        lines.append(f"  {name}: {detail}")
    return "\n".join(lines)


async def run(args) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    previous: list[str] | None = None

    while True:
        try:
            report = await probe(args.base)
        except Exception as exc:  # noqa: BLE001 — бэкенд может и не отвечать
            report = {
                "status": "fail",
                "broken": ["backend"],
                "checks": {"backend": {"detail": f"{type(exc).__name__}: {exc}"}},
            }

        broken = report.get("broken") or []
        text = describe(report)
        log.info(text.replace("\n", " · "))

        # Только на переходе: иначе лежащий ночью стенд к утру пришлёт
        # триста одинаковых сообщений.
        if previous is not None and broken != previous:
            await shout(text)
        previous = broken

        if not args.loop:
            return 1 if broken else 0

        await asyncio.sleep(args.every)


def main() -> int:
    parser = argparse.ArgumentParser(description="Сторож стенда")
    parser.add_argument("--base", default=BASE, help="адрес бэкенда")
    parser.add_argument("--loop", action="store_true", help="крутиться постоянно")
    parser.add_argument("--every", type=int, default=EVERY_SEC, help="период, с")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

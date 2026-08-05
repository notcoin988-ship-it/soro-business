"""Управление вебхуком Telegram (раздел 7.1 ТЗ).

    --info    что сейчас прописано и есть ли ошибки доставки
    --set     прописать PUBLIC_BASE_URL + /webhooks/telegram
    --delete  снять вебхук (нужно перед polling)

Проверять `--info` перед каждым демо: ngrok при перезапуске меняет адрес,
и вебхук замолкает молча — бот просто перестаёт отвечать.

Запуск (из корня soro-business):
    docker compose exec backend python scripts/tg_webhook.py --info
"""

from __future__ import annotations

import argparse
import asyncio
import sys

sys.path.insert(0, "/code")

from app.channels.telegram import get_bot, set_webhook  # noqa: E402
from app.config import settings  # noqa: E402


async def show() -> None:
    bot = get_bot()
    me = await bot.get_me()
    info = await bot.get_webhook_info()

    print(f"бот              : @{me.username} (id {me.id})")
    print(f"PUBLIC_BASE_URL  : {settings.PUBLIC_BASE_URL}")
    print(f"вебхук           : {info.url or '— не задан, работает polling —'}")
    print(f"секрет проверяется: {'да' if settings.TELEGRAM_WEBHOOK_SECRET else 'НЕТ'}")
    print(f"в очереди         : {info.pending_update_count}")
    if info.last_error_message:
        print(f"последняя ошибка : {info.last_error_date} — {info.last_error_message}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", action="store_true")
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--info", action="store_true")
    args = parser.parse_args()

    bot = get_bot()
    try:
        if args.set:
            print("вебхук прописан:", await set_webhook())
        elif args.delete:
            await bot.delete_webhook(drop_pending_updates=True)
            print("вебхук снят")
        await show()
    finally:
        await bot.session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

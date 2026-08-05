"""Запуск Telegram-бота через long polling — для локальной проверки.

ТЗ требует webhook, и он написан (`channels/telegram.py`), но вебхуку
нужен публичный HTTPS через ngrok. Пока адреса нет, тот же обработчик
можно гонять через polling: Telegram сам отдаёт апдейты, ничего наружу
открывать не надо.

Обработчик один и тот же — меняется только доставка. То есть проверка
здесь настоящая, а не «почти как в проде».

Запуск (из корня soro-business):
    docker compose exec backend python scripts/tg_polling.py

Остановка — Ctrl+C. Перед боевым демо переключаться на вебхук:
    docker compose exec backend python scripts/tg_webhook.py --set
"""

from __future__ import annotations

import asyncio
import logging
import sys

sys.path.insert(0, "/code")

from app.channels.telegram import get_bot, get_dispatcher  # noqa: E402


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s"
    )
    bot = get_bot()
    me = await bot.get_me()

    # Вебхук и polling взаимоисключающи: Telegram отдаёт апдейты либо туда,
    # либо сюда. Снимаем вебхук, иначе getUpdates вернёт ошибку 409.
    await bot.delete_webhook(drop_pending_updates=True)

    print(f"бот @{me.username} слушает. Напишите ему в Telegram. Ctrl+C — выход.")
    try:
        await get_dispatcher().start_polling(bot, handle_signals=False)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await bot.session.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nостановлено")

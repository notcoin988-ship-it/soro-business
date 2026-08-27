"""Шина событий между воркерами (Redis pub/sub).

ЗАЧЕМ. SSE-поток виджета и WebSocket оператора — это открытые соединения,
которые держит КОНКРЕТНЫЙ процесс. Ответ бота считает фоновая задача, а
реплику оператора приносит HTTP-запрос, и оба могут оказаться в другом
процессе. Пока бэкенд был один, это работало на словаре в памяти; с двумя
воркерами клиент подключался к одному, событие рождалось в другом, и до
браузера не доходило ничего — виджет молчал, инбокс не обновлялся.

КАК УСТРОЕНО. Событие уходит в Redis-канал; в каждом процессе крутится
подписчик, который отдаёт его локальным получателям — очередям виджета и
сокетам инбокса. Свой же процесс получает событие тем же путём, что и
чужой: одна дорога вместо двух, и «у меня работает, а на проде нет» не
случается.

БЕЗ REDIS ТОЖЕ РАБОТАЕТ. Если подписчик не запущен (тесты, скрипты) или
Redis не отвечает, событие доставляется напрямую локальным получателям.
Это не «тихая деградация»: при одном процессе локальная доставка и есть
правильное поведение, а падать из-за кеша, когда клиент ждёт ответ, — нет.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import suppress

import redis.asyncio as aioredis

from app.config import settings

log = logging.getLogger(__name__)

# Каналы Redis. Имена с префиксом проекта: база может быть общей с чем-то
# ещё, а `inbox` — слишком заманчивое имя, чтобы быть уникальным.
WIDGET = "soro:widget"
INBOX = "soro:inbox"

# Локальные получатели: канал → функция доставки в этом процессе.
_local: dict[str, Callable[[dict], None]] = {}

# Подписчик крутится, только когда приложение живёт целиком (lifespan).
# В тестах и скриптах его нет, и это нормально — см. шапку.
_task: asyncio.Task | None = None
_running = False


def on(channel: str, deliver: Callable[[dict], None]) -> None:
    """Кто в этом процессе получает события канала."""
    _local[channel] = deliver


def _alive() -> bool:
    """Работает ли подписчик В ЭТОМ цикле событий.

    Проверка цикла не перестраховка: `TestClient` из FastAPI поднимает
    приложение со своим циклом в отдельном потоке, и после его закрытия
    оставался включённый флаг и задача от мёртвого цикла. Следующий тест
    честно публиковал в Redis, слушать было некому, и событие пропадало —
    два теста виджета падали по таймауту через раз.
    """
    if not _running or _task is None or _task.done():
        return False
    try:
        return _task.get_loop() is asyncio.get_running_loop()
    except RuntimeError:
        return False


async def publish(channel: str, payload: dict) -> None:
    """Разослать событие всем процессам.

    Асинхронная: у Redis-клиента здесь async-интерфейс, и звать его из
    обработчика запроса надо не блокируя цикл.
    """
    if not _alive():
        _deliver(channel, payload)
        return

    try:
        client = aioredis.from_url(settings.REDIS_URL)
        try:
            await client.publish(channel, json.dumps(payload, ensure_ascii=False))
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001 — см. шапку модуля
        log.warning("шина недоступна (%s), доставляю локально", exc)
        _deliver(channel, payload)


def _deliver(channel: str, payload: dict) -> None:
    deliver = _local.get(channel)
    if deliver is None:
        log.warning("событие в канал %s, а получателя нет", channel)
        return
    deliver(payload)


async def _listen() -> None:
    """Подписчик процесса. Живёт всё время работы приложения."""
    global _running

    while True:
        try:
            client = aioredis.from_url(settings.REDIS_URL)
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            await pubsub.subscribe(*_local.keys())
            _running = True
            log.info("шина подписана на %s", ", ".join(_local))

            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                channel = message["channel"]
                if isinstance(channel, bytes):
                    channel = channel.decode()
                _deliver(channel, json.loads(message["data"]))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — обрыв Redis не повод падать
            # Пока подписчика нет, `publish` доставляет локально: при
            # одном процессе это полностью рабочий режим.
            _running = False
            log.warning("шина отвалилась (%s), переподключаюсь через 5 с", exc)
            await asyncio.sleep(5)


async def start() -> None:
    """Поднять подписчика. Зовётся из lifespan приложения."""
    global _task
    if _task is None:
        _task = asyncio.create_task(_listen())


async def stop() -> None:
    """Погасить подписчика. Порядок важен: сначала снимаем задачу и ждём
    её конца, и только потом сбрасываем флаг. Наоборот — гонка: задача
    успевала подключиться и снова поднять флаг уже после остановки."""
    global _task, _running

    task, _task = _task, None
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    _running = False

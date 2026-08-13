"""Пределы на публичных эндпоинтах.

ЗАЧЕМ. Виджет открыт всему интернету — его адрес лежит в исходниках сайта
банка. Пока лимитов не было, скрипт в цикле мог сжечь токены модели, забить
базу мусорными диалогами и заодно разбудить оператора сотней эскалаций.

СЧЁТЧИК В REDIS, А НЕ В ПАМЯТИ. В памяти он считает на каждый процесс
отдельно, и при нескольких воркерах лимит молча умножается на их число.
Redis в проекте уже есть — очередь индексации и токены склейки живут там же.

ЕСЛИ REDIS ЛЁГ — ПРОПУСКАЕМ. Ограничитель не должен становиться единой
точкой отказа: клиент банка, которому не ответили из-за проблем с нашим
кешем, — хуже, чем клиент, который прислал лишний вопрос. В лог при этом
пишем: молчаливое снятие защиты — плохая идея.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException
from redis import Redis

from app.config import settings

log = logging.getLogger(__name__)

# Окно счётчика. Минута — самый понятный интервал: «20 сообщений в минуту»
# читается без калькулятора и в логе, и в разговоре с банком.
WINDOW_SEC = 60

KEY_PREFIX = "rate:"


def _redis() -> Redis:
    """Клиент на вызов — как в `channels/widget.py`; см. причину там."""
    return Redis.from_url(settings.REDIS_URL)


def hit(bucket: str, limit: int | None = None) -> None:
    """Отметить обращение. Превышен лимит — HTTP 429.

    `bucket` — то, что считаем: обычно `widget:<uid>`. Считать по одному
    только адресу нельзя: за одним корпоративным NAT сидит целый офис
    банка, и лимит съел бы их всех разом.
    """
    limit = limit if limit is not None else settings.WIDGET_RATE_PER_MIN
    if limit <= 0:  # 0 — «без ограничений», для локальных прогонов
        return

    key = KEY_PREFIX + bucket
    try:
        client = _redis()
        used = client.incr(key)
        if used == 1:
            # Срок ставим только на первом обращении: иначе окно
            # продлевалось бы каждым запросом, и лимит стал бы вечным.
            client.expire(key, WINDOW_SEC)
    except Exception as exc:  # noqa: BLE001 — см. шапку модуля
        log.warning("ограничитель не работает (%s), пропускаю запрос", exc)
        return

    if used > limit:
        # 429 с Retry-After: вежливый клиент подождёт сам, а невежливому
        # ответ всё равно дешевле, чем поход в модель.
        raise HTTPException(
            status_code=429,
            detail="слишком часто, подождите минуту",
            headers={"Retry-After": str(WINDOW_SEC)},
        )


def check_length(text: str) -> str:
    """Обрезать вопрос по потолку. Возвращает то, что уйдёт дальше.

    Обрезаем, а не отказываем: человек, вставивший в поле три страницы
    договора, хотел спросить по делу, и отказ он воспримет как поломку.
    Поиску всё равно достаётся только начало.
    """
    limit = settings.MESSAGE_MAX_CHARS
    if limit > 0 and len(text) > limit:
        log.info("вопрос обрезан: %s символов при потолке %s", len(text), limit)
        return text[:limit]
    return text

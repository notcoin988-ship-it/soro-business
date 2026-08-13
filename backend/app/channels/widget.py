"""Веб-виджет: серверная часть (раздел 7.2 ТЗ).

ОТВЕТСТВЕННОСТЬ: эндпоинты для iframe виджета — приём сообщения, отдача
ответа стримом (SSE), выдача `link_token` для перехода в Telegram.

ИДЕНТИФИКАЦИЯ: cookie-uuid в `channel_identities.external_id`, канал
`widget`. Регистрации нет — это в разделе 1.2 явно вынесено за скобки.

СТРИМИНГ: ответ идёт кусками, как в прототипе на экране 03. Клиент видит
текст по мере генерации, а не через шесть секунд молчания.

СКЛЕЙКА С TELEGRAM: кнопка «Продолжить в Telegram» открывает
`t.me/<бот>?start=<link_token>`; токен — 24 случайных символа, живёт в
Redis 15 минут.

ЗАВИСИМОСТИ: core.dialog, core.linking, Redis, models.
СТАТУС: готова выдача `link_token` и склейка контактов по нему; чат со
стримом — следующим шагом.
"""

from __future__ import annotations

import logging
import secrets

from redis import Redis

from app.config import settings

log = logging.getLogger(__name__)

CHANNEL = "widget"

# 15 минут — столько живёт токен. Меньше не стоит: человек нажимает
# «Продолжить в Telegram», а дальше ищет приложение, логинится, отвлекается
# на звонок. Больше — тоже: ссылку с токеном пересылают, и чем дольше он
# живёт, тем выше шанс, что перепиской завладеет не тот человек.
TOKEN_TTL = 15 * 60

# 18 случайных байт в base64url — ровно 24 символа, как в разделе 5.1.
TOKEN_BYTES = 18

KEY_PREFIX = "link:"


def _redis() -> Redis:
    """Клиент создаётся на вызов, а не на модуль.

    Так же сделано в `api/console.py` с очередью: модульный клиент
    подключался бы при импорте, и тесты HTTP-слоя потребовали бы живой
    Redis ради проверки, которая до него не доходит.
    """
    return Redis.from_url(settings.REDIS_URL)


def issue_token(identity_id: int) -> str:
    """Выдать одноразовый токен для идентичности виджета."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    _redis().setex(KEY_PREFIX + token, TOKEN_TTL, str(identity_id))
    return token


def take_token(token: str) -> int | None:
    """Обменять токен на идентичность виджета. Токен сгорает.

    Одноразовость важнее удобства: ссылка `t.me/bot?start=<токен>` уходит в
    историю браузера и в буфер обмена, и второй переход по ней должен
    приводить к обычному новому диалогу, а не к чужой переписке.
    """
    if not token:
        return None

    # GETDEL — одна операция вместо GET + DEL: между ними два перехода по
    # одной ссылке успели бы склеиться оба.
    raw = _redis().getdel(KEY_PREFIX + token)
    if raw is None:
        log.info("токен склейки не найден или истёк")
        return None
    return int(raw)


def telegram_link(token: str) -> str:
    """Ссылка для кнопки «Продолжить в Telegram»."""
    return f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start={token}"

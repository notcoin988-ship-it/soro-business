"""Telegram-бот (раздел 7.1 ТЗ) — первый из каналов.

Канал ничего не решает: принимает апдейт, отдаёт текст в `core.dialog` и
отправляет обратно то, что вернуло ядро. Вся логика — там, иначе бот в
Telegram и бот в WhatsApp начнут вести себя по-разному.

РЕЖИМ. ТЗ требует webhook, и он здесь основной: `POST /webhooks/telegram`
с проверкой секретного заголовка. Но вебхуку нужен публичный HTTPS, то
есть ngrok; пока его нет, тот же обработчик работает через long polling
(`scripts/tg_polling.py`). Код обработки один и тот же — отличается только
способ доставки апдейта.

БЕЗОПАСНОСТЬ. Telegram присылает `X-Telegram-Bot-Api-Secret-Token` с тем
значением, которое мы задали при setWebhook. Без проверки этого заголовка
эндпоинт открыт всему интернету: кто угодно сможет прислать «сообщение от
клиента» и получить ответ бота.

ГРАБЛИ: ngrok при перезапуске меняет адрес, и вебхук молча замолкает.
Перед демо проверять `getWebhookInfo` (`scripts/tg_webhook.py --info`).
"""

from __future__ import annotations

import logging

from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from fastapi import APIRouter, Header, HTTPException, Request

from app.channels import widget
from app.config import settings
from app.core import feedback, linking
from app.core.dialog import handle_incoming, resolve_identity
from app.db import SessionLocal
from app.models import ChannelIdentity

log = logging.getLogger(__name__)

CHANNEL = "telegram"
WEBHOOK_PATH = "/webhooks/telegram"

# Приветствие на /start. Двуязычное намеренно: клиент банка в Таджикистане
# пишет и по-таджикски, и по-русски, и заранее неизвестно, как начнёт.
GREETING = (
    "Салом! Ман боти Бонки Эсхата. Аз рӯи ҳуҷҷатҳои бонк ҷавоб медиҳам.\n"
    "Здравствуйте! Я бот Банка Эсхата, отвечаю по документам банка.\n\n"
    "Саволатонро нависед — напишите ваш вопрос."
)

NON_TEXT_REPLY = (
    "Ман ҳоло танҳо матнро мефаҳмам. Лутфан саволатонро нависед.\n"
    "Пока я понимаю только текст — напишите вопрос словами."
)

router = APIRouter(tags=["telegram"])

_bot: Bot | None = None
_dispatcher: Dispatcher | None = None


def get_bot() -> Bot:
    """Один экземпляр Bot на процесс: внутри живёт HTTP-сессия."""
    global _bot
    if _bot is None:
        if not settings.TELEGRAM_BOT_TOKEN:
            raise RuntimeError("TELEGRAM_BOT_TOKEN не задан в .env")
        _bot = Bot(
            token=settings.TELEGRAM_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=None),
        )
    return _bot


def get_dispatcher() -> Dispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = Dispatcher()
        _dispatcher.message.register(on_message)
        _dispatcher.callback_query.register(on_rating)
    return _dispatcher


# --- оценка работы оператора -----------------------------------------------
#
# Кнопки, а не текст: попроси человека написать «5» — и это «5» уедет в
# поиск по базе знаний как обычный вопрос. Callback приходит отдельным
# типом апдейта и до ядра не доходит.

RATE_PREFIX = "rate:"

THANKS = "Раҳмат барои баҳо!\nСпасибо за оценку!"


def rating_keyboard(message_id: int) -> InlineKeyboardMarkup:
    """Две кнопки под прощальным сообщением."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👍 Понравилось",
                    callback_data=f"{RATE_PREFIX}{message_id}:{feedback.UP}",
                ),
                InlineKeyboardButton(
                    text="👎 Нет",
                    callback_data=f"{RATE_PREFIX}{message_id}:{feedback.DOWN}",
                ),
            ]
        ]
    )


async def store_rating(data: str) -> bool:
    """Разобрать `callback_data` и записать оценку.

    Отдельно от обработчика намеренно: разбор строки и запись в базу
    проверяются без сети, без объекта `CallbackQuery` и без живого бота.
    """
    if not data.startswith(RATE_PREFIX):
        return False

    try:
        message_id, score = data[len(RATE_PREFIX) :].split(":")
        async with SessionLocal() as session:
            saved = await feedback.record(session, int(message_id), int(score))
            await session.commit()
    except Exception:  # noqa: BLE001 — кнопка не повод ронять обработчик
        log.exception("не удалось записать оценку по callback %r", data)
        return False

    return saved is not None


async def on_rating(query: CallbackQuery) -> None:
    """Клиент нажал кнопку оценки."""
    saved = await store_rating(query.data or "")

    # Всплывающее уведомление вместо нового сообщения: диалог закрыт, и
    # дописывать в него ещё одну реплику незачем.
    await query.answer(THANKS if saved else "Не получилось, извините")
    # Кнопки убираем — оценку ставят один раз.
    with suppress(Exception):
        await query.message.edit_reply_markup(reply_markup=None)


def display_name(message: Message) -> str | None:
    user = message.from_user
    if user is None:
        return None
    name = " ".join(p for p in (user.first_name, user.last_name) if p)
    return name or user.username


async def link_from_widget(token: str, message: Message) -> bool:
    """Склеить контакт этого telegram-пользователя с контактом виджета.

    Возвращает, состоялась ли склейка. Неудача — это норма: токен
    одноразовый и живёт 15 минут, а `/start` с чужим или протухшим хвостом
    прилетает от кого угодно. Клиенту об этом знать незачем: он увидит
    обычное приветствие и начнёт разговор заново — хуже, чем могло быть,
    но не сломано.

    ОШИБКИ ГЛУШИМ. Этот вызов стоит на пути `/start`: если Redis лежит или
    склейка упала, поздороваться бот обязан всё равно.
    """
    try:
        identity_id = widget.take_token(token)
        if identity_id is None:
            return False

        async with SessionLocal() as session:
            widget_identity = await session.get(ChannelIdentity, identity_id)
            if widget_identity is None:
                log.warning("токен склейки указывает на исчезнувшую идентичность")
                return False

            telegram_identity = await resolve_identity(
                session,
                workspace_id=widget_identity.workspace_id,
                channel=CHANNEL,
                external_id=str(message.from_user.id),
                display_name=display_name(message),
            )
            await linking.link_identities(
                session, first=widget_identity, second=telegram_identity
            )
            await session.commit()
    except Exception:  # noqa: BLE001 — приветствие важнее склейки
        log.exception("не удалось склеить контакты по токену")
        return False

    return True


async def answer_for(message: Message) -> str | None:
    """Что ответить на сообщение. Отдельно от отправки — чтобы тестировать
    без сети и без Telegram.

    `None` означает «молчим»: диалогом занимается оператор.
    """
    if message.text is None:
        return NON_TEXT_REPLY

    if message.text.startswith("/start"):
        # Хвост после /start — это link_token из виджета (раздел 5.1):
        # клиент нажал «Продолжить в Telegram», и оба канала должны стать
        # одним человеком.
        token = message.text[len("/start") :].strip()
        if token:
            await link_from_widget(token, message)
        return GREETING

    async with SessionLocal() as session:
        reply = await handle_incoming(
            session,
            channel=CHANNEL,
            external_id=str(message.from_user.id),
            text=message.text,
            display_name=display_name(message),
        )

    return None if reply is None else reply.text


async def on_message(message: Message) -> None:
    text = await answer_for(message)
    if text is not None:
        await message.answer(text)


@router.post(WEBHOOK_PATH)
async def telegram_webhook(
    request: Request,
    secret: str | None = Header(None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> dict:
    """Точка входа Telegram. Отвечать надо быстро и всегда 200.

    Если вернуть ошибку, Telegram будет слать этот апдейт снова и снова —
    клиент получит один и тот же ответ несколько раз.
    """
    if settings.TELEGRAM_WEBHOOK_SECRET and secret != settings.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="неверный секрет вебхука")

    update = Update.model_validate(await request.json(), context={"bot": get_bot()})
    try:
        await get_dispatcher().feed_update(get_bot(), update)
    except Exception:  # noqa: BLE001 — падение обработчика не повод для ретраев
        log.exception("ошибка обработки апдейта %s", update.update_id)

    return {"ok": True}


async def set_webhook() -> str:
    """Прописать адрес вебхука в Telegram."""
    url = settings.PUBLIC_BASE_URL.rstrip("/") + WEBHOOK_PATH
    if not url.startswith("https://"):
        raise RuntimeError(
            "Telegram принимает только https, а PUBLIC_BASE_URL = "
            f"{settings.PUBLIC_BASE_URL!r}. Запустите ngrok и подставьте адрес."
        )
    await get_bot().set_webhook(
        url=url,
        secret_token=settings.TELEGRAM_WEBHOOK_SECRET or None,
        drop_pending_updates=True,
    )
    return url

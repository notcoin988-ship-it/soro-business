"""Интерактивная проверка ядра: поговорить с ботом руками.

Каналов ещё нет, а ядро уже работает — этот скрипт подключается к нему
напрямую и показывает не только ответ, но и то, что легло в базу: где
оригинал, где маска, сколько заняло, в каком статусе диалог.

Запуск (из корня soro-business):
    docker compose exec backend python scripts/chat.py

Команды прямо в диалоге:
    /db          что лежит в базе по текущему диалогу
    /who         кто я сейчас: канал, внешний id, контакт, диалог
    /tg <id>     переключиться на Telegram с этим id (по умолчанию demo-tg)
    /web <id>    то же для виджета — видно, что диалог получится ДРУГОЙ,
                 пока контакты не склеены
    /wa <id>     то же для WhatsApp
    /take        оператор берёт диалог: после этого бот молчит
    /return      вернуть боту
    /reset       стереть все диалоги и контакты воркспейса
    /quit        выход

Всё пишется в настоящую базу — на то и проверка. `/reset` убирает следы.
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "/code")

from sqlalchemy import delete, select  # noqa: E402

from app.core.dialog import handle_incoming  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    ChannelIdentity,
    Contact,
    Conversation,
    Message,
    Workspace,
)

WORKSPACE = "eskhata-demo"

# то, чем удобно пробовать: карта, телефон, счёт, просьба про оператора
HINTS = [
    "Салом! Фоизи амонати «Ояндасоз» чанд аст?",
    "Корти ман 5058 1234 5678 9012 кор намекунад",
    "Занг занед ба +992 93 123 45 67",
    "Ҳисоб 20206972000123456789",
    "Оператор дихед",
]

# Кодировки терминала. Windows-консоль отдаёт введённый текст в своей
# кодовой странице (cp866 для русской локали), а Python в контейнере
# декодирует его как UTF-8 и подставляет суррогаты \udcXX вместо
# нераспознанных байт. Дальше такую строку не принимает уже Postgres:
# «surrogates not allowed». Чиним на входе — иначе поговорить с ботом
# по-таджикски нельзя.
FALLBACK_ENCODINGS = ("utf-8", "cp866", "cp1251", "koi8-r")

# Дополнительные буквы таджикского алфавита — их наличие тоже считаем
# признаком удачного разбора.
TAJIK_LETTERS = "ӣӯҳҷғқӢӮҲҶҒҚ"


def _cyrillic_score(text: str) -> int:
    """Сколько в строке кириллицы. Чем больше, тем вероятнее, что кодировку
    угадали правильно."""
    return sum(
        1
        for ch in text
        if "А" <= ch <= "я" or ch in "ёЁ" or ch in TAJIK_LETTERS
    )


_warned_about_encoding = False


def fix_encoding(text: str) -> str:
    """Починить строку, пришедшую в чужой кодировке.

    Перебирать кодировки «до первой успешной» нельзя: cp1251 и cp866
    однобайтовые и принимают почти любые байты, поэтому первая же из них
    «успешно» вернёт кашу. Выбираем ту, что дала больше кириллицы.
    """
    global _warned_about_encoding

    if not any("\udc80" <= ch <= "\udcff" for ch in text):
        return text

    if not _warned_about_encoding:
        _warned_about_encoding = True
        print(
            f"{ROSE}Терминал отдаёт текст не в UTF-8.{OFF} Русский я восстановлю,\n"
            f"{GREY}но букв ӣ ӯ ҳ ҷ ғ қ в кодировках cp866 и cp1251 нет — они\n"
            f"теряются ещё в консоли. Лечится один раз:  chcp 65001{OFF}"
        )

    raw = text.encode("utf-8", "surrogateescape")
    best = raw.decode("utf-8", "replace")
    best_score = -1

    for encoding in FALLBACK_ENCODINGS:
        try:
            candidate = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        score = _cyrillic_score(candidate)
        if score > best_score:
            best, best_score = candidate, score

    return best


GREY = "\033[90m"
ROSE = "\033[38;5;204m"
OK = "\033[38;5;79m"
OFF = "\033[0m"


class Session:
    def __init__(self) -> None:
        self.channel = "telegram"
        self.external_id = "demo-tg"

    def switch(self, channel: str, external_id: str | None) -> None:
        self.channel = channel
        self.external_id = external_id or f"demo-{channel[:2]}"


async def ensure_workspace(session) -> Workspace:
    workspace = await session.scalar(
        select(Workspace).where(Workspace.slug == WORKSPACE)
    )
    if workspace is None:
        workspace = Workspace(slug=WORKSPACE, name="Банк Эсхата")
        session.add(workspace)
        await session.commit()
        print(f"{GREY}воркспейс {WORKSPACE} создан{OFF}")
    return workspace


async def current_conversation(session, state: Session) -> Conversation | None:
    identity = await session.scalar(
        select(ChannelIdentity).where(
            ChannelIdentity.channel == state.channel,
            ChannelIdentity.external_id == state.external_id,
        )
    )
    if identity is None:
        return None
    return await session.scalar(
        select(Conversation)
        .where(Conversation.contact_id == identity.contact_id)
        .order_by(Conversation.last_msg_at.desc())
    )


async def show_db(session, state: Session) -> None:
    conversation = await current_conversation(session, state)
    if conversation is None:
        print(f"{GREY}диалога ещё нет — напишите что-нибудь{OFF}")
        return

    messages = (
        await session.scalars(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.id)
        )
    ).all()

    print(f"{GREY}диалог #{conversation.id}, статус {conversation.status}, "
          f"сообщений {len(messages)}{OFF}")
    for m in messages:
        mark = "клиент" if m.role == "user" else m.role
        print(f"  {GREY}[{m.channel}/{mark}]{OFF} {m.text}")
        if m.text != m.text_masked:
            print(f"      {ROSE}в модель уходит:{OFF} {m.text_masked}")


async def show_who(session, state: Session) -> None:
    conversation = await current_conversation(session, state)
    identity = await session.scalar(
        select(ChannelIdentity).where(
            ChannelIdentity.channel == state.channel,
            ChannelIdentity.external_id == state.external_id,
        )
    )
    print(
        f"{GREY}канал {state.channel} · id {state.external_id} · "
        f"контакт {identity.contact_id if identity else '—'} · "
        f"диалог {conversation.id if conversation else '—'} "
        f"({conversation.status if conversation else '—'}){OFF}"
    )


async def set_status(session, state: Session, status: str) -> None:
    conversation = await current_conversation(session, state)
    if conversation is None:
        print(f"{GREY}диалога ещё нет{OFF}")
        return
    conversation.status = status
    await session.commit()
    print(f"{GREY}диалог #{conversation.id} → {status}{OFF}")


async def reset(session) -> None:
    await session.execute(delete(Message))
    await session.execute(delete(Conversation))
    await session.execute(delete(ChannelIdentity))
    await session.execute(delete(Contact))
    await session.commit()
    print(f"{GREY}всё стёрто{OFF}")


async def main() -> int:
    async with SessionLocal() as session:
        await ensure_workspace(session)
        state = Session()

        print(f"\n{ROSE}Ядро Soro Business{OFF} — ответ пока эхо, но маршрут настоящий.")
        print(f"{GREY}Команды: /db /who /tg /web /wa /take /return /reset /quit{OFF}")
        print(f"{GREY}Попробуйте, например:{OFF}")
        for hint in HINTS:
            print(f"{GREY}  · {hint}{OFF}")
        print()

        while True:
            try:
                line = fix_encoding(input(f"{state.channel}> ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0

            if not line:
                continue

            if line in ("/quit", "/exit"):
                return 0
            if line == "/db":
                await show_db(session, state)
                continue
            if line == "/who":
                await show_who(session, state)
                continue
            if line == "/reset":
                await reset(session)
                continue
            if line == "/take":
                await set_status(session, state, "operator")
                continue
            if line == "/return":
                await set_status(session, state, "bot")
                continue
            if line.startswith(("/tg", "/web", "/wa")):
                command, _, arg = line.partition(" ")
                channel = {"/tg": "telegram", "/web": "widget", "/wa": "whatsapp"}[
                    command
                ]
                state.switch(channel, arg.strip() or None)
                await show_who(session, state)
                continue
            if line.startswith("/"):
                print(f"{GREY}неизвестная команда{OFF}")
                continue

            reply = await handle_incoming(
                session,
                channel=state.channel,
                external_id=state.external_id,
                text=line,
                workspace_slug=WORKSPACE,
            )

            if reply is None:
                print(f"{GREY}бот молчит: диалогом занимается оператор{OFF}")
                continue

            colour = OK if not reply.escalated else ROSE
            print(f"{colour}бот>{OFF} {reply.text}")
            tail = f"{GREY}   {reply.latency_ms} мс · диалог #{reply.conversation_id}"
            if reply.escalated:
                tail += " · ЭСКАЛАЦИЯ"
            print(tail + OFF)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Клиент Soro API (раздел 6.6 ТЗ): промпт, стриминг, разбор ответа.

ОТВЕТСТВЕННОСТЬ: собрать промпт из найденных фрагментов, сходить в модель
по OpenAI-совместимому API и отдать ответ по кускам (стриминг нужен
«Площадке» и виджету).

ВХОД:  вопрос клиента (уже замаскированный!), фрагменты от `core.rag`.
ВЫХОД: поток кусков ответа + телеметрия (латентность, токены).

ЖЁСТКИЕ ПРАВИЛА:

* SYSTEM_PROMPT и USER_TEMPLATE берутся из раздела 6.6 ДОСЛОВНО;
* формат фрагмента в промпте: `[N] (Документ, стр. X): текст`;
* в модель уходит ТОЛЬКО `messages.text_masked` — никогда `text`;
* маркер `[ESCALATE]` в ответе вырезается, текст без него уходит клиенту,
  а эскалация запускается с причиной `pii_topic` или `no_answer`.

ОТКЛОНЕНИЕ: ТЗ фиксирует модель `soro-27b-fp8`, а сервер отдаёт
`zehnlab/SoroLLM-27B-Instruct-GPTQ-int4` — брать имя из `SORO_MODEL`.

ССЫЛКИ В ОТВЕТЕ. Правило 3 промпта требует `[1]`, `[2]` после каждого
факта. Наружу эти номера идут по-разному (раздел 6.6):
* консоль и виджет — кликабельные бейджи, фронт получает `chunks_used` и
  рисует `<sup class="cite">1</sup>` с подсказкой «документ, страница»;
* Telegram — строкой-футером «Манбаъ: Тарифҳо 2026, саҳ. 4».
Поэтому здесь номера НЕ трогаем: канал сам решает, как их показать. Но
`chunks_used` возвращается упорядоченным — без него номер не во что
превратить.

ЗАВИСИМОСТИ: `SORO_API_URL`, `SORO_API_KEY`, `core.rag.Hit`.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx

from app.config import settings
from app.core.rag import Hit

# --- промпт раздела 6.6, дословно; менять только вместе с ТЗ --------------

SYSTEM_PROMPT = """Ту — Soro, ёрдамчии расмии «{bank_name}».
Қоидаҳо / Правила (нарушать НЕЛЬЗЯ):
1. Отвечай ТОЛЬКО на основе фрагментов в блоке <docs>.
   Если ответа там нет — скажи, что соединишь со
   специалистом, и добавь в конец строку [ESCALATE].
2. Отвечай на языке вопроса: таджикский -> таджикский,
   русский -> русский, смешанный -> как удобнее клиенту.
3. После каждого факта ставь ссылку вида [1], [2] — номер
   фрагмента, из которого факт взят.
4. Никогда не называй данные счетов, карт, балансов —
   у тебя нет к ним доступа. Такие вопросы -> [ESCALATE].
5. Не выдумывай числа. Нет числа во фрагменте — нет числа
   в ответе.
6. Тон: вежливый, тёплый, короткие абзацы, без канцелярита.
7. В конце, где уместно, задай один уточняющий вопрос."""

USER_TEMPLATE = """<docs>
{fragments}
</docs>

Вопрос клиента: {question}"""

# --- разбор ответа --------------------------------------------------------

ESCALATE_MARKER = "[ESCALATE]"
# Маркер вырезаем вместе с пробелами и переводами строк вокруг: модель
# ставит его «в конец», но регулярно — с новой строки, а иногда посреди
# абзаца. Оставить пустую строку в конце ответа некрасиво, а в Telegram
# ещё и заметно.
ESCALATE_RE = re.compile(r"\s*\[ESCALATE\]\s*", re.I)
# Номера ссылок [1], [12] — по ним канал строит бейджи и футер источников.
CITE_RE = re.compile(r"\[(\d{1,2})\]")

# Причины эскалации из раздела 6.6.
REASON_NO_ANSWER = "no_answer"
REASON_PII_TOPIC = "pii_topic"

# Темы, на которых эскалация — не «нет ответа», а «нет доступа к данным
# клиента» (правило 4 промпта). Причина важна для инбокса: оператор
# по ней сразу понимает, надо ли лезть в АБС.
PII_TOPIC_RE = re.compile(
    r"баланс|мандаи|списал|гирифта шуд|лимит|перевод|интиқол|"
    r"заявк|дархост|одобри|отказ|карт[уаы]\b|корт|счёт|ҳисоб",
    re.I,
)

# Стриминг обязателен: норматив 6 секунд на весь ответ, и первые буквы
# должны появиться раньше. Таймаут на подключение жёсткий, на чтение —
# длинный: модель генерирует ответ постепенно.
LLM_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


@dataclass
class Answer:
    """Готовый ответ модели вместе с телеметрией."""

    text: str
    # фрагменты, на которые модель сослалась, по порядку номеров [1], [2];
    # из них канал строит бейджи и футер «Манбаъ: …»
    chunks_used: list[int] = field(default_factory=list)
    escalate: bool = False
    reason: str | None = None
    latency_ms: int = 0
    # первые буквы — отдельная метрика: на «Площадке» видно именно её
    first_token_ms: int = 0


def format_fragments(hits: list[Hit]) -> str:
    """Фрагменты в блок <docs>. Формат из ТЗ: `[N] (Документ, стр. X): текст`.

    Нумерация с единицы и по порядку выдачи поиска — на эти номера модель
    ссылается в тексте, и `chunks_used[N-1]` обязан указывать на тот же
    фрагмент. Разъедется нумерация — бейдж откроет чужой документ.

    У страниц сайта `page` пустой (см. решение про `chunks.page`), тогда
    скобка со страницей не пишется вовсе: «(Тарифы, стр. None)» в промпте
    модель честно перепишет в ответ.
    """
    lines = []
    for number, hit in enumerate(hits, start=1):
        where = hit.title
        if hit.page is not None:
            where = f"{hit.title}, стр. {hit.page}"
        lines.append(f"[{number}] ({where}): {hit.text}")
    return "\n\n".join(lines)


def build_messages(question: str, hits: list[Hit], bank_name: str) -> list[dict]:
    """Сообщения для chat/completions."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(bank_name=bank_name)},
        {
            "role": "user",
            "content": USER_TEMPLATE.format(
                fragments=format_fragments(hits), question=question
            ),
        },
    ]


def cited_chunk_ids(text: str, hits: list[Hit]) -> list[int]:
    """`chunks.id` тех фрагментов, на которые модель сослалась.

    Порядок — как в ответе: первый упомянутый [N] идёт первым. Номер вне
    диапазона выданных фрагментов молча пропускаем: модель иногда
    придумывает [4], когда фрагментов три, и падать из-за этого незачем.
    """
    used: list[int] = []
    for match in CITE_RE.finditer(text):
        index = int(match.group(1)) - 1
        if 0 <= index < len(hits):
            chunk_id = hits[index].chunk_id
            if chunk_id not in used:
                used.append(chunk_id)
    return used


def parse_answer(raw: str, hits: list[Hit], question: str) -> tuple[str, bool, str | None]:
    """Ответ модели → (текст клиенту, нужна ли эскалация, причина)."""
    escalate = bool(ESCALATE_RE.search(raw))
    text = ESCALATE_RE.sub(" ", raw).strip() if escalate else raw.strip()
    # два пробела после вырезания маркера посреди абзаца
    text = re.sub(r"  +", " ", text)

    reason = None
    if escalate:
        reason = (
            REASON_PII_TOPIC if PII_TOPIC_RE.search(question) else REASON_NO_ANSWER
        )
    return text, escalate, reason


async def stream_answer(
    question: str,
    hits: list[Hit],
    *,
    bank_name: str = "Банк Эсхата",
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[str]:
    """Куски ответа по мере генерации.

    Отдаём именно куски, а не готовый текст: «Площадка» и виджет обязаны
    показывать ответ по мере набора, а Telegram раз в 1,5 секунды
    редактирует сообщение (раздел 7.1). Собрать целое из кусков легко,
    разобрать целое на куски — нет.
    """
    payload = {
        "model": settings.SORO_MODEL,
        "messages": build_messages(question, hits, bank_name),
        "max_tokens": settings.SORO_MAX_TOKENS,
        "temperature": settings.SORO_TEMPERATURE,
        "stream": True,
    }
    url = settings.SORO_API_URL.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {settings.SORO_API_KEY}"}

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=LLM_TIMEOUT)
    try:
        async with client.stream(
            "POST", url, json=payload, headers=headers
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    # битый кусок SSE — не повод ронять весь ответ
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                piece = (choices[0].get("delta") or {}).get("content")
                if piece:
                    yield piece
    finally:
        if own_client:
            await client.aclose()


async def answer(
    question: str,
    hits: list[Hit],
    *,
    bank_name: str = "Банк Эсхата",
    client: httpx.AsyncClient | None = None,
) -> Answer:
    """Собранный ответ. Обёртка над `stream_answer` для каналов без стрима.

    Telegram и WhatsApp тоже стримят, но в БД и в тесты нужен готовый
    текст, поэтому сборка живёт здесь, а не размазана по каналам.
    """
    started = time.monotonic()
    first_token_ms = 0
    pieces: list[str] = []

    async for piece in stream_answer(
        question, hits, bank_name=bank_name, client=client
    ):
        if not pieces:
            first_token_ms = int((time.monotonic() - started) * 1000)
        pieces.append(piece)

    raw = "".join(pieces)
    text, escalate, reason = parse_answer(raw, hits, question)

    return Answer(
        text=text,
        chunks_used=cited_chunk_ids(text, hits),
        escalate=escalate,
        reason=reason,
        latency_ms=int((time.monotonic() - started) * 1000),
        first_token_ms=first_token_ms,
    )

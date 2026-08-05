"""Нарезка документа на фрагменты (раздел 6.3 ТЗ).

Параметры зафиксированы документом и менять их нельзя:

* единица нарезки — **абзац**; абзацы склеиваются в чанк, пока длина
  меньше 400 токенов;
* перехлёст — последние **60 токенов** предыдущего чанка повторяются в
  начале следующего;
* абзац длиннее 400 токенов режется **по предложениям**;
* в начало каждого чанка дописывается шапка
  «Документ: {title}. Страница {page}.» — она заметно помогает поиску;
* токены считаются токенайзером самой модели эмбеддингов,
  `AutoTokenizer.from_pretrained('BAAI/bge-m3')`.

Почему перехлёст вообще нужен: ответ на вопрос клиента часто лежит на
стыке двух абзацев («ставка 14,5%» в одном, «минимальная сумма 500
сомонӣ» в следующем). Без перехлёста такой стык рвётся ровно посередине
ответа, и поиск находит половину.

Чанкер ничего не знает ни про базу, ни про HTTP: на входе — страницы от
парсера, на выходе — список фрагментов. Эмбеддинги и запись в БД делает
`ingest/worker.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from app.ingest.parsers import ParsedPage

# --- параметры ТЗ, менять только вместе с документом ----------------------
CHUNK_TOKENS = 400
OVERLAP_TOKENS = 60
TOKENIZER_NAME = "BAAI/bge-m3"

# Разделители предложений из ТЗ: . ! ? и «таджикское ?».
# В таджикской кириллице вопросительный знак обычный, но в документах
# банков встречается и арабский U+061F — он остался от старых наборов.
SENTENCE_END = re.compile(r"(?<=[.!?؟])\s+")

# Запасной счётчик, если токенайзер недоступен (нет сети и нет кеша).
# 3,5 символа на токен — замер bge-m3 на таджикско-русском тексте банка.
CHARS_PER_TOKEN = 3.5


@dataclass(frozen=True)
class Chunk:
    """Готовый фрагмент. `ord` — порядковый номер внутри документа."""

    page: int | None
    ord: int
    text: str


@lru_cache(maxsize=1)
def _tokenizer():
    """Токенайзер bge-m3. Кешируется: загрузка стоит секунды.

    Возвращает None, если модель недоступна — тогда считаем приблизительно
    по символам. Падать здесь нельзя: без сети индексация всё равно должна
    отработать, пусть и с чуть менее точными границами.
    """
    try:
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(TOKENIZER_NAME)
    except Exception:
        return None


def count_tokens(text: str) -> int:
    tokenizer = _tokenizer()
    if tokenizer is None:
        return max(1, int(len(text) / CHARS_PER_TOKEN))
    return len(tokenizer.encode(text, add_special_tokens=False))


def header_for(title: str, page: int | None) -> str:
    """Контекст-шапка чанка (формат задан ТЗ дословно).

    Для веб-страниц номера страницы нет — тогда шапка короче, без «Страница».
    Врать «Страница None» нельзя: шапка уходит в модель как факт.
    """
    if page is None:
        return f"Документ: {title}."
    return f"Документ: {title}. Страница {page}."


def split_paragraph(paragraph: str) -> list[str]:
    """Абзац длиннее лимита режется по предложениям.

    Если и одно предложение длиннее лимита (таблица, склеенная в строку,
    или скан без пунктуации) — режем по словам: лучше грубая граница, чем
    фрагмент, который не влезет в модель.
    """
    if count_tokens(paragraph) <= CHUNK_TOKENS:
        return [paragraph]

    out: list[str] = []
    buffer: list[str] = []
    size = 0

    for sentence in SENTENCE_END.split(paragraph):
        sentence = sentence.strip()
        if not sentence:
            continue
        weight = count_tokens(sentence)

        if weight > CHUNK_TOKENS:
            if buffer:
                out.append(" ".join(buffer))
                buffer, size = [], 0
            out.extend(_split_by_words(sentence))
            continue

        if size + weight > CHUNK_TOKENS and buffer:
            out.append(" ".join(buffer))
            buffer, size = [], 0
        buffer.append(sentence)
        size += weight

    if buffer:
        out.append(" ".join(buffer))
    return out


def _split_by_words(text: str) -> list[str]:
    out: list[str] = []
    buffer: list[str] = []
    size = 0
    for word in text.split():
        weight = count_tokens(word)
        if size + weight > CHUNK_TOKENS and buffer:
            out.append(" ".join(buffer))
            buffer, size = [], 0
        buffer.append(word)
        size += weight
    if buffer:
        out.append(" ".join(buffer))
    return out


def _overlap_tail(paragraphs: list[str]) -> tuple[list[str], int]:
    """Хвост предыдущего чанка длиной не больше OVERLAP_TOKENS."""
    tail: list[str] = []
    size = 0
    for piece in reversed(paragraphs):
        weight = count_tokens(piece)
        if size + weight > OVERLAP_TOKENS:
            break
        tail.insert(0, piece)
        size += weight
    return tail, size


def chunk_pages(pages: list[ParsedPage], title: str) -> list[Chunk]:
    """Страницы документа → фрагменты со сквозной нумерацией.

    Чанк не переходит границу страницы: иначе сноска «стр. 4» в ответе
    бота будет указывать не туда, а сноски — часть демо. Цена — несколько
    коротких фрагментов на границах, это дешевле неверной ссылки.
    """
    chunks: list[Chunk] = []
    number = 0

    for page in pages:
        paragraphs: list[str] = []
        for raw in page.text.splitlines():
            line = raw.strip()
            if line:
                paragraphs.extend(split_paragraph(line))

        if not paragraphs:
            continue

        header = header_for(title, page.page)
        buffer: list[str] = []
        size = 0

        for paragraph in paragraphs:
            weight = count_tokens(paragraph)
            if size + weight > CHUNK_TOKENS and buffer:
                number += 1
                chunks.append(
                    Chunk(page.page, number, header + "\n" + "\n".join(buffer))
                )
                buffer, size = _overlap_tail(buffer)
            buffer.append(paragraph)
            size += weight

        if buffer:
            number += 1
            chunks.append(
                Chunk(page.page, number, header + "\n" + "\n".join(buffer))
            )

    return chunks

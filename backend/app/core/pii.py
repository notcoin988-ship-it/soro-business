"""Маскирование ПДн (раздел 8.2 ТЗ). Вызывается ДО модели, всегда.

В `messages.text` хранится оригинал (оператор должен видеть настоящий
номер), в `messages.text_masked` — маска; в модель уходит ТОЛЬКО
`text_masked`.

ОТКЛОНЕНИЕ ОТ ТЗ. Раздел 8.2 приводит четыре готовых регэкспа. На 109
тестах они дали 26 дефектов трёх видов:

1. **Утечка.** Номера счетов (20 цифр) и IBAN в ТЗ не предусмотрены вообще.
   Чужие паттерны откусывали от счёта кусок, и строка выглядела
   обработанной, а первые 11 цифр оставались в открытом виде.
2. **Порча текста.** Телефонный паттерн — это фактически «9 цифр подряд»,
   причём жадный: номер договора `2026001234` превращался в `2[PHONE]`, а
   ИНН помечался как телефон. Модель получала мусор и отвечала мимо.
3. **Склейка слов.** Маска карты захватывала пробел после последней цифры:
   `[CARD]кор намекунад`.

Исходные регэкспы ТЗ оставлены ниже в комментарии — по ним видно, что
именно изменилось.

    # карты: 16 цифр с пробелами/дефисами или слитно
    (re.compile(r"\\b(?:\\d[ -]?){13,19}\\b"), "[CARD]"),
    # телефоны Таджикистана: +992 xx xxx xx xx и вариации
    (re.compile(r"(?:\\+?992[\\s-]?)?\\d{2}[\\s-]?\\d{3}[\\s-]?"
                r"\\d{2}[\\s-]?\\d{2}\\b"), "[PHONE]"),
    # паспорт РТ: серия A + 8 цифр (латиница или кириллица А)
    (re.compile(r"\\b[AА]\\s?\\d{8}\\b", re.I), "[PASSPORT]"),
    # ИНН 9 цифр
    (re.compile(r"\\b\\d{9}\\b"), "[INN]"),

Как устроена замена сейчас. Регэксп находит только «кандидата» — цепочку
цифр с разделителями. Решение принимается по КОЛИЧЕСТВУ цифр в цепочке, а
не по её виду: 13–19 цифр это карта, ровно 20 — счёт, ровно 9 — телефон.
Всё остальное (10, 11, 12, 30 цифр) — не ПДн, и текст не трогается.

Именно счёт цифр отличает номер карты от номера договора, а регэксп из ТЗ
их различить не мог в принципе.
"""

from __future__ import annotations

import re
from typing import Callable

# Разделители внутри номера. У карт бывают точки (5058.1234.5678.9012) и
# табуляция — при копировании из Excel; у телефонов скобки: (93) 123-45-67.
CARD_SEPS = r"[ \t. -]"
PHONE_SEPS = r"[ \t ()-]"

# Кандидат: цифра, потом цифры и разделители, потом снова цифра.
# Границы (?<!\d) и (?!\d) обязательны: без них 30-значная цепочка
# распадётся на «карту» и остаток, и получится частичное маскирование.
CARD_CANDIDATE = re.compile(rf"(?<!\d)\d(?:{CARD_SEPS}*\d){{7,}}(?!\d)")
PHONE_CANDIDATE = re.compile(rf"(?<!\d)\+?\d(?:{PHONE_SEPS}*\d){{6,}}(?!\d)")

# IBAN Таджикистана: TJ, две контрольные цифры, дальше 20 знаков.
IBAN_RE = re.compile(r"\bTJ[ -]?\d{2}(?:[ -]?[A-Z0-9]){16,24}\b", re.I)

# Паспорт РТ: серия A (латиница или кириллица) + 8 цифр.
PASSPORT_RE = re.compile(r"(?<![A-ZА-Я\d])[AА][ -]?\d{8}(?!\d)", re.I)

# ИНН отличается от телефона только контекстом: и то и другое — 9 цифр.
# Поэтому ИНН ловим по слову-указателю рядом («ИНН», «РМА»), а всё
# остальное девятизначное считаем телефоном.
# Между словом и числом бывают слова: «ИНН нашей компании 987654321».
# Окно в 20 символов — компромисс: длиннее начнёт цеплять посторонние
# номера («ИНН не знаю, звоните 931234567»), короче не покроет обычные
# формулировки. Ошибка тут стоит недорого: и ИНН, и телефон всё равно
# маскируются, страдает только точность метки в аналитике.
INN_RE = re.compile(r"(ИНН|РМА)([^\d]{0,20}?)(\d{9})(?!\d)", re.I)

CARD_DIGITS = range(13, 20)  # 13..19
ACCOUNT_DIGITS = 20
PHONE_DIGITS = 9
COUNTRY_CODE = "992"


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def _mask_groups(
    text: str, *, max_digits: int, accept: Callable[[str], bool], label: str
) -> str:
    """Маскирует внутри кандидата те группы цифр, что проходят проверку.

    Группы набираются жадно, пока не упрутся в `max_digits`. Это нужно для
    случая «две карты подряд через пробел»: 32 цифры одной цепочкой не
    подходят ни под что, а по 16 — обе маскируются.
    """
    groups = list(re.finditer(r"\d+", text))
    out: list[str] = []
    pos = 0
    i = 0

    while i < len(groups):
        total = 0
        j = i
        while j < len(groups) and total + len(groups[j].group()) <= max_digits:
            total += len(groups[j].group())
            j += 1

        span = text[groups[i].start() : groups[j - 1].end()] if j > i else ""
        if j > i and accept(_digits(span)):
            out.append(text[pos : groups[i].start()])
            out.append(label)
            pos = groups[j - 1].end()
            i = j
        else:
            i += 1

    out.append(text[pos:])
    return "".join(out)


def _mask_cards_and_accounts(match: re.Match) -> str:
    """Счёт (ровно 20 цифр) и карта (13–19). Порядок важен: сначала счёт,
    иначе первые 16 цифр счёта уйдут как карта, а хвост останется открытым.
    """
    text = _mask_groups(
        match.group(0),
        max_digits=ACCOUNT_DIGITS,
        accept=lambda d: len(d) == ACCOUNT_DIGITS,
        label="[ACCOUNT]",
    )
    return _mask_groups(
        text,
        max_digits=max(CARD_DIGITS),
        accept=lambda d: len(d) in CARD_DIGITS,
        label="[CARD]",
    )


def _accept_phone(digits: str) -> bool:
    if len(digits) == PHONE_DIGITS:
        return True
    # +992 и девять цифр номера
    return len(digits) == len(COUNTRY_CODE) + PHONE_DIGITS and digits.startswith(
        COUNTRY_CODE
    )


def _mask_phones(match: re.Match) -> str:
    return _mask_groups(
        match.group(0),
        max_digits=len(COUNTRY_CODE) + PHONE_DIGITS,
        accept=_accept_phone,
        label="[PHONE]",
    )


# Порядок применения — единственное, что здесь нельзя переставлять:
# IBAN → счета и карты → паспорт → ИНН → телефоны.
# ИНН стоит перед телефоном, потому что оба — девять цифр, и без
# слова-указателя рядом ИНН неотличим от номера.
PATTERNS: list[tuple[re.Pattern, str | Callable]] = [
    (IBAN_RE, "[IBAN]"),
    (CARD_CANDIDATE, _mask_cards_and_accounts),
    (PASSPORT_RE, "[PASSPORT]"),
    (INN_RE, r"\1\2[INN]"),
    (PHONE_CANDIDATE, _mask_phones),
]


def mask(text: str) -> str:
    for rx, repl in PATTERNS:
        text = rx.sub(repl, text)
    return text

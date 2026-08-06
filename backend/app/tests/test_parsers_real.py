"""Парсеры на настоящих документах банка (критерий сдачи недели 2).

Синтетические фикстуры из `factories.py` проверяют ветки кода. Здесь
проверяется другое: что реальный PDF банка — с таблицами тарифов,
надстрочными сносками, пустыми страницами и таджикской диакритикой — не
ломает парсер и отдаёт текст, по которому вообще можно искать.

Три документа с сайта `eskhata.com`, происхождение и находки — в
`data/README.md`. Файлы лежат в git: банк документы меняет и удаляет,
а тест обязан быть воспроизводимым.

Скана среди PDF банка не нашлось — все с текстовым слоем, поэтому
OCR-ветка остаётся на синтетическом скане в `test_parsers.py`.
"""

from __future__ import annotations

import pytest

from app.ingest import parsers
from app.ingest.chunker import CHUNK_TOKENS, chunk_pages, count_tokens
from app.ingest.parsers import ParsedPage, parse_file, parse_pdf
from app.tests import data

needs_ocr = pytest.mark.skipif(
    not parsers.ocr_available(),
    reason="нет tesseract с языками tgk+rus (есть в образе backend)",
)

TAJIK_LETTERS = set("ӣӯҳҷғқӢӮҲҶҒҚ")


# ---------------------------------------------------------------------------
# Тарифы физлиц — главный документ демо
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tarify() -> list[ParsedPage]:
    return parse_pdf(data.TARIFY_FIZ_RU)


def test_tarify_all_pages_read(tarify):
    """12 страниц, нумерация с единицы и без дыр — по ней строятся ссылки
    «[1] Тарифы, стр. 4» в ответе бота."""
    assert [p.page for p in tarify] == list(range(1, 13))


def test_tarify_has_no_ocr_pages(tarify):
    """Документ текстовый. Если хоть одна страница ушла в OCR — либо порог
    40 символов задевает нормальную страницу, либо файл подменили."""
    assert [p.page for p in tarify if p.ocr] == []


def test_tarify_keeps_prices_and_services(tarify):
    """То, ради чего документ вообще индексируется: услуга и её цена."""
    text = "\n".join(p.text for p in tarify)
    assert "Открытие счёта физическим лицам" in text
    assert "комиссия не взимается" in text
    assert "5 сомони" in text


def test_tarify_row_keeps_service_next_to_price(tarify):
    """Строка таблицы разбирается в порядке чтения: услуга и цена остаются
    соседними строками.

    Это и есть проверка «реальный PDF не рассыпается»: если pymupdf начнёт
    отдавать текст по колонкам, все цены соберутся в конце страницы, чанкер
    разложит их по разным фрагментам, и ответ «сколько стоит перевод»
    станет невозможным в принципе.
    """
    page = next(p for p in tarify if "Открытие счёта физическим лицам" in p.text)
    lines = [line.strip() for line in page.text.splitlines() if line.strip()]
    service = lines.index("Открытие счёта физическим лицам")
    assert lines[service + 1] == "комиссия не взимается"


def test_tarify_page_number_is_where_it_belongs(tarify):
    """Раздел «Общие положения» по оглавлению на 3-й странице — проверяем,
    что нумерация ParsedPage совпадает с нумерацией внутри документа, а не
    сдвинута на титул."""
    page = next(p for p in tarify if "ОБЩИЕ ПОЛОЖЕНИЯ" in p.text)
    assert page.page == 3


# ---------------------------------------------------------------------------
# Кредитный договор на таджикском — диакритика
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def shartnoma() -> list[ParsedPage]:
    return parse_pdf(data.SHARTNOMAI_QARZI_TJ)


def test_shartnoma_all_pages_read(shartnoma):
    assert [p.page for p in shartnoma] == list(range(1, 8))


def test_shartnoma_keeps_tajik_diacritics(shartnoma):
    """ӣ ӯ ҳ ҷ ғ қ обязаны дожить до текста.

    На синтетике это проверяется своим шрифтом; здесь — настоящий файл,
    собранный чужим Word'ом. Если диакритика потеряется, поиск по
    таджикским документам молча деградирует: «қарз» и «карз» для
    `to_tsvector('simple')` разные слова.
    """
    assert "ШАРТНОМАИ ҚАРЗӢ" in shartnoma[0].text
    assert "Қарзгир" in shartnoma[0].text


def test_shartnoma_is_tajik_on_every_page(shartnoma):
    """Диакритика не только на титуле: документ таджикский целиком."""
    for page in shartnoma:
        assert TAJIK_LETTERS & set(page.text), f"страница {page.page} без ӣӯҳҷғқ"


# ---------------------------------------------------------------------------
# Договор карточного счёта — пустая страница внутри документа
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dogovor() -> list[ParsedPage]:
    """Требует tesseract: на пустой странице 7 парсер уходит в OCR-ветку.

    Пропуск проверяется внутри, а не маркером: `skipif` на фикстуре
    молча игнорируется, и тест упал бы на импорте pytesseract.
    """
    if not parsers.ocr_available():
        pytest.skip("нет tesseract с языками tgk+rus (есть в образе backend)")
    return parse_pdf(data.DOGOVOR_KART_SCHETA_RU)


@needs_ocr
def test_dogovor_empty_page_does_not_shift_numbering(dogovor):
    """Страница 7 пуста по-настоящему: ни текста, ни картинки.

    Парсер обязан отдать её пустой строкой, а не выбросить: иначе всё, что
    дальше, поедет на единицу, и ссылка «стр. 8» в ответе бота будет
    указывать на страницу 9 настоящего договора.
    """
    assert [p.page for p in dogovor] == list(range(1, 12))
    assert dogovor[6].text == ""
    assert "Приложение №2" in dogovor[7].text


@needs_ocr
def test_dogovor_empty_page_marked_as_ocr(dogovor):
    """Пустая страница помечена `ocr=True` — парсер пробовал распознать.

    Флаг важен на приёмке: он отличает «страница правда пустая» от
    «текст не извлёкся».
    """
    assert dogovor[6].ocr is True
    assert [p.page for p in dogovor if p.ocr] == [7]


@needs_ocr
def test_dogovor_keeps_bank_details(dogovor):
    """Реквизиты банка — частый вопрос клиента, они не должны потеряться."""
    text = "\n".join(p.text for p in dogovor)
    assert "SWIFT: EJSATJ22" in text
    assert "Корреспондентский счёт: 20402972457071" in text


# ---------------------------------------------------------------------------
# Сквозняк: реальный документ проходит парсер и чанкер
# ---------------------------------------------------------------------------


def test_real_document_goes_through_chunker(tarify):
    """Парсер отдаёт то, что чанкер умеет резать по правилам 6.3.

    Проверяем на настоящем документе: длина фрагмента в пределах 400
    токенов и ни один фрагмент не пустой. Пустой чанк — это вектор без
    смысла, который потом всплывает в выдаче на любой вопрос.
    """
    chunks = chunk_pages(tarify, "Тарифы для обслуживания физических лиц")

    assert chunks, "из 12 страниц тарифов не получилось ни одного фрагмента"
    for chunk in chunks:
        # первая строка — шапка «Документ: … Страница …», её чанкер
        # дописывает сверх лимита; лимит 6.3 считается по телу
        body = chunk.text.split("\n", 1)[1]
        assert body.strip()
        assert count_tokens(body) <= CHUNK_TOKENS


def test_parse_file_dispatches_real_pdf():
    pages = parse_file(data.TARIFY_FIZ_RU)
    assert isinstance(pages[0], ParsedPage)
    assert pages[0].page == 1

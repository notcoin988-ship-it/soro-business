"""Тесты парсеров (неделя 2, раздел 6.2 ТЗ).

Проверяются ВСЕ ветки parsers.py: pdf с текстовым слоем, pdf-скан через
OCR, docx с таблицей, xlsx с несколькими листами, html для краулера и
диспетчер по расширению.

Документы здесь синтетические (см. factories.py): фикстура на каждую ветку
собирается ровно под неё, и содержимое подобрано под настоящий кейс —
таджикский с диакритикой, проценты, суммы, таблицы курсов. Настоящие
документы банка проверяются отдельно, в `test_parsers_real.py`.

OCR-тесты пропускаются, если в системе нет tesseract с языками tgk+rus:
внутри контейнера backend они есть (Dockerfile), на голой Windows — нет.
"""

from __future__ import annotations

import pytest

from app.ingest import parsers
from app.ingest.parsers import (
    OCR_MIN_CHARS,
    ParsedPage,
    UnsupportedDocument,
    parse_docx,
    parse_file,
    parse_html,
    parse_pdf,
    parse_xlsx,
)
from app.tests import factories

needs_font = pytest.mark.skipif(
    factories.find_font() is None,
    reason="в системе нет TTF-шрифта с кириллицей — PDF-фикстуру не собрать",
)
needs_ocr = pytest.mark.skipif(
    not parsers.ocr_available(),
    reason="нет tesseract с языками tgk+rus (есть в образе backend)",
)


# ---------------------------------------------------------------------------
# PDF — текстовый слой
# ---------------------------------------------------------------------------


@pytest.fixture
def text_pdf(tmp_path):
    return factories.make_pdf(
        tmp_path / "tarify.pdf",
        [factories.PDF_PAGE_TJ, factories.PDF_PAGE_RU],
    )


@needs_font
def test_pdf_pages_numbered_from_one(text_pdf):
    pages = parse_pdf(text_pdf)
    assert [p.page for p in pages] == [1, 2]


@needs_font
def test_pdf_keeps_tajik_diacritics(text_pdf):
    """Главное, что может сломаться на кириллице: ӣ ӯ ҳ ҷ ғ қ.

    Если PDF собран не тем шрифтом или текст прочитан не в той кодировке,
    диакритика превращается в пустые квадраты — и весь поиск по таджикским
    документам молча деградирует.
    """
    text = parse_pdf(text_pdf)[0].text
    assert "Ояндасоз" in text
    assert "сомонӣ" in text
    assert "Маблағи" in text
    assert "14,5%" in text


@needs_font
def test_pdf_page_two_is_russian(text_pdf):
    assert "зарплатной карты" in parse_pdf(text_pdf)[1].text


@needs_font
def test_pdf_text_page_does_not_go_to_ocr(text_pdf):
    """Страница с текстом НЕ должна попадать в OCR: он в разы дороже."""
    assert all(p.ocr is False for p in parse_pdf(text_pdf))


# ---------------------------------------------------------------------------
# PDF — скан, OCR-ветка
# ---------------------------------------------------------------------------


@needs_font
def test_scan_page_has_almost_no_text_layer(tmp_path):
    """Предпосылка OCR-ветки: у скана текстового слоя нет.

    Тест сторожит сам порог: если фикстура вдруг начнёт отдавать текст,
    следующий тест будет проверять не то, что думает.
    """
    import fitz

    path = factories.make_scan_pdf(tmp_path / "scan.pdf")
    with fitz.open(path) as doc:
        assert len(doc[0].get_text("text").strip()) < OCR_MIN_CHARS


@needs_font
@needs_ocr
def test_scan_page_goes_through_ocr(tmp_path):
    path = factories.make_scan_pdf(tmp_path / "scan.pdf")
    pages = parse_pdf(path)

    assert len(pages) == 1
    assert pages[0].ocr is True
    # OCR не обязан распознать всё дословно, поэтому проверяем, что текст
    # вообще появился и в нём есть узнаваемые куски. Требовать полного
    # совпадения — значит тестировать качество tesseract, а не свой код.
    text = pages[0].text.lower()
    assert len(text) >= OCR_MIN_CHARS
    assert "фоиз" in text or "500" in text


@needs_font
def test_short_text_page_is_treated_as_scan(tmp_path):
    """Порог 40 символов — из ТЗ. Короткая страница уходит в OCR даже с
    текстовым слоем; это осознанное поведение, а не баг."""
    path = factories.make_pdf(tmp_path / "short.pdf", [factories.PDF_PAGE_SHORT])
    assert len(factories.PDF_PAGE_SHORT) < OCR_MIN_CHARS
    if not parsers.ocr_available():
        pytest.skip("нет tesseract")
    assert parse_pdf(path)[0].ocr is True


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------


@pytest.fixture
def docx_file(tmp_path):
    return factories.make_docx(tmp_path / "pravila.docx")


def test_docx_single_page_none(docx_file):
    """У DOCX страниц нет — page обязан быть None, а не 1."""
    pages = parse_docx(docx_file)
    assert len(pages) == 1
    assert pages[0].page is None


def test_docx_keeps_order_of_paragraphs_and_table(docx_file):
    """Таблица должна стоять между абзацами, а не уехать в конец.

    python-docx отдаёт `paragraphs` и `tables` двумя отдельными списками —
    наивная склейка «сначала все абзацы, потом все таблицы» рвёт связь
    таблицы с её разделом, и чанк с курсами теряет заголовок.
    """
    lines = parse_docx(docx_file)[0].text.splitlines()
    intro = lines.index("Курси асъор дар санаи 04.08.2026:")
    usd = next(i for i, line in enumerate(lines) if "USD" in line)
    tail = next(i for i, line in enumerate(lines) if "44 600 60 60" in line)
    assert intro < usd < tail


def test_docx_table_repeats_header_in_every_row(docx_file):
    """Правило 6.2: заголовок таблицы повторяется в каждой строке."""
    lines = parse_docx(docx_file)[0].text.splitlines()
    usd = next(line for line in lines if "USD" in line)
    eur = next(line for line in lines if "EUR" in line)

    assert usd == "Валюта: USD | Купля: 10,90 | Продажа: 11,05"
    assert eur.startswith("Валюта: EUR")


def test_docx_drops_empty_paragraphs(docx_file):
    assert "" not in parse_docx(docx_file)[0].text.splitlines()


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------


@pytest.fixture
def xlsx_file(tmp_path):
    return factories.make_xlsx(tmp_path / "tarify.xlsx")


def test_xlsx_page_is_sheet_number(xlsx_file):
    """Правило 6.2: page = номер листа."""
    pages = parse_xlsx(xlsx_file)
    assert [p.page for p in pages] == [1, 2]


def test_xlsx_row_format_is_header_colon_value(xlsx_file):
    first = parse_xlsx(xlsx_file)[0].text.splitlines()
    assert first[0] == "Маҳсулот: Ояндасоз; Фоиз: 14,5%; Ҳадди ақал: 500"


def test_xlsx_skips_empty_rows(xlsx_file):
    """Пустая строка внутри данных не должна давать пустой чанк."""
    lines = parse_xlsx(xlsx_file)[0].text.splitlines()
    assert len(lines) == 2
    assert all(line.strip() for line in lines)


def test_xlsx_integer_is_not_float(xlsx_file):
    """openpyxl отдаёт 500 как 500.0 — в тексте документа это мусор,
    и поиск по «500 сомонӣ» такой чанк уже не найдёт."""
    text = parse_xlsx(xlsx_file)[0].text
    assert "500" in text
    assert "500.0" not in text


def test_xlsx_second_sheet_parsed(xlsx_file):
    second = parse_xlsx(xlsx_file)[1].text
    assert "Амалиёт: Интиқол дар дохили бонк; Комиссия: 0%" in second


# ---------------------------------------------------------------------------
# HTML — то, что зовёт краулер
# ---------------------------------------------------------------------------

HTML = """
<html><head><title>  Амонати Ояндасоз | Бонк  </title></head>
<body>
  <header>Логотип банка</header>
  <nav><a href="/tarify">Тарифҳо</a><a href="/about">Дар бораи мо</a></nav>
  <main>
    <h1>Амонати «Ояндасоз»</h1>
    <p>Фоизи солона — 14,5%.</p>
    <p>Маблағи ҳадди ақал — 500 сомонӣ.</p>
  </main>
  <footer>© 2026 Бонк</footer>
  <script>var x = 'мусор из скрипта';</script>
</body></html>
"""


def test_html_title_collapsed():
    title, _ = parse_html(HTML)
    assert title == "Амонати Ояндасоз | Бонк"


def test_html_keeps_main_content():
    _, text = parse_html(HTML)
    assert "Фоизи солона — 14,5%." in text
    assert "500 сомонӣ" in text


@pytest.mark.parametrize(
    "junk",
    ["Логотип банка", "Дар бораи мо", "© 2026 Бонк", "мусор из скрипта"],
)
def test_html_drops_service_tags(junk):
    """ТЗ: выкидывать nav, header, footer, script.

    Без этого меню сайта попадёт в каждый чанк каждой страницы, и поиск
    начнёт выдавать «Дар бораи мо» на любой вопрос.
    """
    _, text = parse_html(HTML)
    assert junk not in text


def test_html_falls_back_to_body_without_main():
    _, text = parse_html("<html><body><p>Матни оддӣ</p></body></html>")
    assert text == "Матни оддӣ"


def test_html_title_falls_back_to_h1():
    title, _ = parse_html("<html><body><h1>Сарлавҳа</h1></body></html>")
    assert title == "Сарлавҳа"


def test_html_lines_are_not_glued():
    """Абзацы должны остаться отдельными строками: чанкер режет по абзацам,
    склеенный текст он нарезать не сможет."""
    _, text = parse_html(HTML)
    assert len(text.splitlines()) >= 3


# ---------------------------------------------------------------------------
# диспетчер
# ---------------------------------------------------------------------------


@needs_font
def test_parse_file_dispatches_by_suffix(tmp_path, docx_file, xlsx_file):
    pdf = factories.make_pdf(tmp_path / "a.pdf", [factories.PDF_PAGE_RU])
    assert isinstance(parse_file(pdf)[0], ParsedPage)
    assert parse_file(docx_file)[0].page is None
    assert parse_file(xlsx_file)[0].page == 1


def test_parse_file_rejects_unknown_suffix(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("матн", encoding="utf-8")
    with pytest.raises(UnsupportedDocument):
        parse_file(path)


def test_parse_file_rejects_fake_docx(tmp_path):
    """Переименованный .doc или обрезанная загрузка: расширение правильное,
    внутри не zip. Ошибка должна быть понятной — она уйдёт в
    documents.error и попадёт человеку на экран «База знаний»."""
    path = tmp_path / "broken.docx"
    path.write_bytes(b"\xd0\xcf\x11\xe0 not a zip")
    with pytest.raises(UnsupportedDocument):
        parse_file(path)

"""Прогон парсера по РЕАЛЬНЫМ PDF банка (критерий сдачи недели 2).

Тимлид обещал три реальных PDF и не дал. Но их нашёл собственный краулер:
на сайте банка лежат открытые тарифы и правила обслуживания. Этот скрипт
качает их и прогоняет через `parse_file` — то, чего синтетические фикстуры
проверить не могут: настоящую вёрстку, многоколоночные таблицы, сканы,
таджикский вперемешку с русским.

Автотестами это НЕ становится: тесты не должны зависеть от сети и от того,
что банк не переложил файл. Здесь — разовая проверка, результат идёт в
report.md.

Запуск (из корня soro-business):
    docker compose exec backend python scripts/check_real_pdf.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, "/code")

import httpx  # noqa: E402

from app.ingest.parsers import OCR_MIN_CHARS, parse_file  # noqa: E402

# найдены прогоном scripts/crawl_site.py по https://eskhata.tj/
#
# Второй адрес оставлен в процентной кодировке НАМЕРЕННО: имя файла на
# сервере записано в форме NFD — «й» там это «и» + U+0306 (%D0%B8%CC%86).
# Собранная строка «…предпринимателей.pdf» даёт 404. Это же ждёт ingest при
# сохранении файла в /data/uploads: Linux хранит имя байт в байт, а Windows
# и macOS нормализуют по-своему.
PDFS = {
    "Тарифы физлиц": "https://eskhata.com/files/Тарифы_на_обслуживание_физических_лиц.pdf",
    "Тарифы юрлиц": (
        "https://eskhata.com/files/%D0%A2%D0%B0%D1%80%D0%B8%D1%84%D1%8B_%D0%BD"
        "%D0%B0_%D0%BE%D0%B1%D1%81%D0%BB%D1%83%D0%B6%D0%B8%D0%B2%D0%B0%D0%BD"
        "%D0%B8%D0%B5_%D1%8E%D1%80%D0%B8%D0%B4%D0%B8%D1%87%D0%B5%D1%81%D0%BA"
        "%D0%B8%D1%85_%D0%BB%D0%B8%D1%86_%D0%B8_%D1%87%D0%B0%D1%81%D1%82%D0"
        "%BD%D1%8B%D1%85_%D0%BF%D1%80%D0%B5%D0%B4%D0%BF%D1%80%D0%B8%D0%BD%D0"
        "%B8%D0%BC%D0%B0%D1%82%D0%B5%D0%BB%D0%B5%D0%B8%CC%86.pdf"
    ),
    # Ссылка на «Қоидаҳои хизматрасонии комплексии бонкӣ…» с сайта банка
    # отдаёт 404 — битая ссылка на их стороне (наш обход насчитал 88 таких).
    # Вместо неё берём документ, который выглядит сканом: он и нужен, чтобы
    # проверить OCR-ветку на настоящей бумаге, а не на синтетике.
    "График приёма граждан": "https://eskhata.com/upload/medialibrary/95e/57jnhitvbs0zs2mudf56e9imidzpm9tf/Приема граждан со стороны руководящих работников ОАО (2).pdf",
    "Годовой отчёт 2023": "https://eskhata.com/upload/iblock/2f0/e1s64mw4zt9vi321vsjz48v51msjihbs/Годовой отчет 2023.pdf",
}

DEST = Path("/tmp/real_pdf")


def download() -> dict[str, Path]:
    DEST.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        for name, url in PDFS.items():
            path = DEST / (name.replace(" ", "_") + ".pdf")
            if not path.exists():
                print(f"качаю: {name}")
                try:
                    response = client.get(url)
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    # один недоступный файл не повод не проверить остальные
                    print(f"  ! не скачался: {type(exc).__name__} {exc}")
                    continue
                path.write_bytes(response.content)
            out[name] = path
    return out


def main() -> int:
    files = download()
    print()

    for name, path in files.items():
        size_kb = path.stat().st_size / 1024
        print("=" * 66)
        print(f"{name}  ({size_kb:.0f} КБ)")
        print("=" * 66)

        started = time.monotonic()
        pages = parse_file(path)
        elapsed = time.monotonic() - started

        ocr = [p for p in pages if p.ocr]
        empty = [p for p in pages if not p.text.strip()]
        chars = sum(len(p.text) for p in pages)
        tj = sum(p.text.count(ch) for p in pages for ch in "ӣӯҳҷғқӢӮҲҶҒҚ")

        print(f"страниц:            {len(pages)}")
        print(f"через OCR (<{OCR_MIN_CHARS} симв.): {len(ocr)}")
        print(f"пустых после парсинга: {len(empty)}")
        print(f"символов:           {chars}")
        print(f"из них тадж. букв:  {tj}")
        print(f"время парсинга:     {elapsed:.1f} сек "
              f"({elapsed / max(len(pages), 1):.2f} сек/стр.)")

        sample = next((p for p in pages if len(p.text) > 200), pages[0] if pages else None)
        if sample is not None:
            print(f"--- стр. {sample.page}, первые 300 символов ---")
            print(sample.text[:300].replace("\n", " ⏎ "))
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

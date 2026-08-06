"""Замер времени индексации 40-страничного PDF (критерий сдачи недели 2).

Норматив раздела 6 ТЗ: «загруженный 40-страничный PDF индексируется
< 3 минут на CPU». Проверяем и называем честную цифру — отдельным пунктом
ловушек недели 2 сказано этого не откладывать.

Что меряется (три фазы реального ingest):
  1. парсинг PDF  — parsers.parse_pdf, при необходимости OCR;
  2. нарезка      — параметры 6.3: 400 токенов, перехлёст 60, шапка
                    «Документ: … Страница …», токенайзер BAAI/bge-m3;
  3. эмбеддинги   — POST EMBEDDINGS_URL/embed батчами по 32.

Запись в БД не меряется: ingest_document — зона Разраба A, его ещё нет.
Вклад INSERT'ов в общее время на порядки меньше эмбеддингов, но в отчёте
это оговорено честно.

Нарезка здесь — замерочная, НЕ замена ingest/chunker.py. Как только
появится настоящий чанкер, импорт меняется на него, и цифра уточняется.

Запуск (из корня soro-business):
    docker compose exec backend python scripts/bench_index.py
    docker compose exec backend python scripts/bench_index.py --pages 40
    docker compose exec backend python scripts/bench_index.py --pdf /data/uploads/real.pdf
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, "/code")

import httpx  # noqa: E402

from app.config import settings  # noqa: E402
from app.ingest.parsers import parse_pdf  # noqa: E402

# параметры 6.3 — зафиксированы ТЗ
CHUNK_TOKENS = 400
OVERLAP_TOKENS = 60
BATCH = 32
LIMIT_SEC = 180  # «< 3 минут»
NORM_PAGES = 40  # норматив сформулирован на 40 страницах


def load_tokenizer():
    """Токенайзер bge-m3. Без сети — считаем приблизительно по символам."""
    try:
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained("BAAI/bge-m3")
    except Exception as exc:  # нет сети/кеша — не повод не мерить
        print(f"  ! токенайзер bge-m3 недоступен ({type(exc).__name__}), "
              f"считаю токены как символы/3.5 — цифра ориентировочная")
        return None


def count_tokens(tokenizer, text: str) -> int:
    if tokenizer is None:
        return int(len(text) / 3.5) + 1
    return len(tokenizer.encode(text, add_special_tokens=False))


def chunk_pages(pages, title: str, tokenizer) -> list[str]:
    """Замерочная нарезка по правилам 6.3: абзацы → чанки ≤ 400 токенов."""
    chunks: list[str] = []
    for page in pages:
        header = f"Документ: {title}. Страница {page.page}."
        buffer: list[str] = []
        size = 0
        for paragraph in [p for p in page.text.splitlines() if p.strip()]:
            weight = count_tokens(tokenizer, paragraph)
            if size + weight > CHUNK_TOKENS and buffer:
                chunks.append(header + "\n" + "\n".join(buffer))
                # перехлёст: тащим хвост предыдущего чанка
                tail, tail_size = [], 0
                for line in reversed(buffer):
                    line_size = count_tokens(tokenizer, line)
                    if tail_size + line_size > OVERLAP_TOKENS:
                        break
                    tail.insert(0, line)
                    tail_size += line_size
                buffer, size = tail, tail_size
            buffer.append(paragraph)
            size += weight
        if buffer:
            chunks.append(header + "\n" + "\n".join(buffer))
    return chunks


def embed(chunks: list[str]) -> tuple[float, int]:
    """Батчи по 32 в TEI. Возвращает (секунды, размерность вектора)."""
    url = f"{settings.EMBEDDINGS_URL}/embed"
    dim = 0
    started = time.monotonic()
    with httpx.Client(timeout=httpx.Timeout(600.0)) as client:
        for i in range(0, len(chunks), BATCH):
            batch = chunks[i : i + BATCH]
            response = client.post(url, json={"inputs": batch})
            response.raise_for_status()
            vectors = response.json()
            dim = len(vectors[0])
            done = min(i + BATCH, len(chunks))
            print(f"    эмбеддинги: {done}/{len(chunks)}", end="\r", flush=True)
    print(" " * 40, end="\r")
    return time.monotonic() - started, dim


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", help="путь к готовому PDF; иначе генерируется")
    parser.add_argument("--pages", type=int, default=40)
    parser.add_argument(
        "--density",
        type=int,
        default=14,
        help="повторов блока на странице; 14 ≈ 2500 символов, как в реальном "
        "тарифе. 1 — редкая страница, цифра получится завышенно оптимистичной",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="мерить скан (OCR-ветка) вместо текстового PDF",
    )
    args = parser.parse_args()

    if args.pdf:
        path = Path(args.pdf)
        title = path.stem
    elif args.scan:
        from app.tests.factories import make_scan_book

        path = Path("/tmp/bench_scan.pdf")
        print(f"[0] генерирую СКАН на {args.pages} стр. → {path}")
        make_scan_book(path, pages=args.pages)
        title = f"Скан (синтетический, {args.pages} стр.)"
    else:
        from app.tests.factories import make_big_pdf

        path = Path(f"/tmp/bench_{args.pages}p.pdf")
        print(
            f"[0] генерирую тестовый PDF на {args.pages} стр., "
            f"плотность {args.density} → {path}"
        )
        make_big_pdf(path, pages=args.pages, density=args.density)
        title = f"Тарифҳо (синтетический, {args.pages} стр.)"

    size_kb = path.stat().st_size / 1024
    print(f"\nФайл: {path}  ({size_kb:.0f} КБ)")
    print("=" * 62)

    tokenizer = load_tokenizer()

    print("[1] парсинг…")
    t0 = time.monotonic()
    pages = parse_pdf(path)
    parse_sec = time.monotonic() - t0
    ocr_pages = sum(1 for p in pages if p.ocr)
    chars = sum(len(p.text) for p in pages)
    print(f"    {len(pages)} стр., из них через OCR: {ocr_pages}, "
          f"{chars} символов — {parse_sec:.1f} сек")

    print("[2] нарезка…")
    t0 = time.monotonic()
    chunks = chunk_pages(pages, title, tokenizer)
    chunk_sec = time.monotonic() - t0
    print(f"    {len(chunks)} фрагментов — {chunk_sec:.1f} сек")

    print("[3] эмбеддинги…")
    embed_sec, dim = embed(chunks)
    print(f"    {len(chunks)} векторов по {dim} — {embed_sec:.1f} сек")

    total = parse_sec + chunk_sec + embed_sec
    print("=" * 62)
    print(f"ИТОГО {total:.1f} сек на {len(pages)} стр. "
          f"({total / max(len(pages), 1):.1f} сек/стр.)")
    print(f"  парсинг    {parse_sec:6.1f} сек  {parse_sec / total * 100:4.1f}%")
    print(f"  нарезка    {chunk_sec:6.1f} сек  {chunk_sec / total * 100:4.1f}%")
    print(f"  эмбеддинги {embed_sec:6.1f} сек  {embed_sec / total * 100:4.1f}%")
    print(f"\nНорматив ТЗ (раздел 6): < {LIMIT_SEC} сек на 40 страниц — "
          f"{'УКЛАДЫВАЕМСЯ' if total < LIMIT_SEC else 'НЕ УКЛАДЫВАЕМСЯ'}")

    # Пересчёт — главное в этом замере. Время почти целиком уходит на
    # эмбеддинги, а их количество зависит не от числа страниц, а от объёма
    # текста. «40 страниц» без указания плотности — не норматив.
    per_chunk = embed_sec / max(len(chunks), 1)
    per_page_chars = chars / max(len(pages), 1)
    # длину фрагмента берём измеренную, а не теоретическую: она зависит от
    # языка (bge-m3 режет таджикский мельче, чем русский) и от того, что
    # каждая страница начинает новый чанк
    chars_per_chunk = chars / max(len(chunks), 1)
    print("-" * 62)
    print(f"плотность:            {per_page_chars:.0f} символов на страницу")
    print(f"цена фрагмента:       {per_chunk:.2f} сек")
    print(f"длина фрагмента:      {chars_per_chunk:.0f} символов")

    if ocr_pages:
        # для скана правит бал не эмбеддинг, а распознавание
        per_page = parse_sec / max(len(pages), 1)
        est = per_page * NORM_PAGES
        print(f"цена страницы в OCR:  {per_page:.1f} сек")
        print(
            f"прогноз: 40-страничный СКАН → ~{est:.0f} сек только на "
            f"распознавание "
            f"({'ок' if est < LIMIT_SEC else 'ВЫХОД ЗА НОРМАТИВ'})"
        )
        print("Запись в БД в замер не входит (ingest_document ещё не написан).")
        return 0

    for density_chars in (2000, 2500, 3000):
        # NORM_PAGES, а не len(pages): пересчитываем замер на норматив.
        # Раньше здесь стояло len(pages) — прогноз молча считался для
        # длины измеренного файла, и 12-страничный тариф давал «40 стр. →
        # 37 сек» вместо честных ~118.
        est_chunks = max(
            NORM_PAGES, round(density_chars * NORM_PAGES / chars_per_chunk)
        )
        est = est_chunks * per_chunk
        verdict = "ок" if est < LIMIT_SEC else "ВЫХОД ЗА НОРМАТИВ"
        print(
            f"прогноз 40 стр. по {density_chars} симв.: "
            f"~{est_chunks} фрагм. → ~{est:.0f} сек  ({verdict})"
        )
    print("Запись в БД в замер не входит (ingest_document ещё не написан).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

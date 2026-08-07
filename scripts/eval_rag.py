"""Прогон golden set: чем меряется качество поиска (раздел 3.2 ТЗ).

Без этого скрипта порог `RAG_MIN_SCORE` крутится вслепую, а «стало лучше»
остаётся вопросом веры.

Печатает три блока, как требует ТЗ:
  1. recall@3           — в топ-3 фрагментах есть нужный;
  2. точность эскалации — сколько «ответа нет» действительно ушло оператору;
  3. таблица промахов.

Целевые значения ТЗ: recall@3 >= 85%, ложных ответов на «нет в базе» —
РОВНО НОЛЬ. Принцип раздела 3.2: лучше лишняя эскалация, чем выдумка.

ГЛАВНОЕ ПРО ПОРОГ. Каждый вопрос ищется ОДИН раз, с порогом 0, а решение
«ответил / эскалировал» считается потом в питоне для всех порогов сразу.
Поэтому `--sweep` прогоняет весь диапазон 0,60–0,72 за те же 60 запросов,
а не за 780: эмбеддинг вопроса и поиск от порога не зависят, от него
зависит только сравнение.

Запуск (из корня soro-business):
    docker compose exec backend python scripts/eval_rag.py
    docker compose exec backend python scripts/eval_rag.py --sweep
    docker compose exec backend python scripts/eval_rag.py --misses 30
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, "/code")

import yaml  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.core.rag import search  # noqa: E402
from app.models import Chunk, Workspace  # noqa: E402

GOLDEN = Path("/code/tests/golden.yaml")
# Диапазон из раздела 3.2. За его пределы не выходим: ниже 0,60 бот начнёт
# выдумывать, выше 0,72 — эскалировать почти всё.
SWEEP = (0.60, 0.62, 0.64, 0.65, 0.66, 0.68, 0.70, 0.72)
TARGET_RECALL = 0.85


@dataclass
class Case:
    question: str
    expect: str                 # 'answer' | 'escalate'
    must_contain: str | None
    best_score: float = 0.0
    found: bool = False         # must_contain нашёлся в топ-3
    top_titles: tuple[str, ...] = ()
    # Заполняется только в режиме --with-model: отказалась ли модель
    # отвечать, увидев фрагменты (маркер [ESCALATE] правила 1 промпта).
    # None — модель не спрашивали.
    model_refused: bool | None = None


def normalize(value: str) -> str:
    """Сравнение must_contain с текстом фрагмента, терпимое к вёрстке.

    Предупреждение из шапки golden.yaml: значения взяты с сайта дословно, и
    «35 000» против «35000», «15,5» против «15.5» дадут ложные промахи.
    Поэтому убираем пробелы (включая неразрывный, которым Битрикс разделяет
    разряды) и приводим десятичный разделитель к запятой.
    """
    value = value.lower().replace("\xa0", " ")
    value = re.sub(r"(?<=\d)[  ](?=\d)", "", value)   # 35 000 → 35000
    value = re.sub(r"(?<=\d)\.(?=\d)", ",", value)     # 15.5 → 15,5
    return re.sub(r"\s+", " ", value).strip()


def load_cases(path: Path) -> list[Case]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = []
    for item in raw:
        expect = item["expect"]
        if expect not in ("answer", "escalate"):
            raise ValueError(f"неизвестный expect={expect!r} у «{item['question']}»")
        cases.append(
            Case(
                question=item["question"],
                expect=expect,
                must_contain=item.get("must_contain"),
            )
        )
    return cases


async def run_cases(
    session: AsyncSession,
    workspace_id: int,
    cases: list[Case],
    *,
    with_model: bool = False,
    threshold: float = 0.0,
    bank_name: str = "",
):
    """Один поиск на вопрос. Порог не применяем — сравним потом.

    С `--with-model` вопросы, прошедшие порог, дополнительно уходят в
    модель: только так видно работу второго фильтра. Режим дорогой (по
    вызову модели на вопрос), поэтому по умолчанию выключен и порог в нём
    фиксированный — перебирать диапазон, дёргая модель, слишком долго.
    """
    from app.core import llm  # локально: без --with-model он не нужен

    for number, case in enumerate(cases, start=1):
        # min_score=0 — решение принимаем сами, для всех порогов сразу
        result = await search(session, case.question, workspace_id, min_score=0.0)
        case.best_score = result.best_score
        case.top_titles = tuple(hit.title for hit in result.hits)

        if case.must_contain:
            needle = normalize(case.must_contain)
            case.found = any(needle in normalize(hit.text) for hit in result.hits)

        if with_model and result.hits and case.best_score >= effective(case, threshold):
            try:
                answer = await llm.answer(
                    case.question, result.hits, bank_name=bank_name
                )
                case.model_refused = answer.escalate
            except Exception as exc:  # noqa: BLE001
                print(f"\n  ! модель недоступна на «{case.question[:40]}»: {exc}")

        print(f"  {number:>2}/{len(cases)}", end="\r", flush=True)
    print(" " * 20, end="\r")


def recall_at_3(cases: list[Case]) -> tuple[int, int]:
    """Доля вопросов группы А, где нужный фрагмент попал в топ-3.

    От порога НЕ зависит: это качество поиска, а не решения отвечать.
    """
    group = [c for c in cases if c.expect == "answer" and c.must_contain]
    return sum(1 for c in group if c.found), len(group)


def effective(case: "Case", threshold: float) -> float:
    """Порог, применяемый к вопросу. Считается ровно как в core/rag.py —
    расхождение сделало бы калибровку слепой."""
    return threshold


def score_at(cases: list[Case], threshold: float) -> dict:
    """Что получится при данном пороге."""
    answer = [c for c in cases if c.expect == "answer"]
    escalate = [c for c in cases if c.expect == "escalate"]

    # Выдумка: ответа в базе нет, а бот отвечает. Этих должно быть 0.
    #
    # Порог — лишь первый фильтр. Второй — сама модель: правило 1 промпта
    # велит ей поставить [ESCALATE], если ответа во фрагментах нет. Без
    # режима --with-model мы этого не видим и считаем выдумкой всё, что
    # прошло порог, — метрика получается пессимистичной и на калиброванном
    # пороге показывает провал там, где система ведёт себя правильно.
    invented = [
        c
        for c in escalate
        if c.best_score >= effective(c, threshold) and c.model_refused is not True
    ]
    # ложная эскалация: ответ есть и найден, но порог его отсёк
    false_escalations = [
        c for c in answer if c.found and c.best_score < effective(c, threshold)
    ]
    answered_right = [
        c for c in answer if c.found and c.best_score >= effective(c, threshold)
    ]

    return {
        "threshold": threshold,
        "invented": invented,
        "false_escalations": false_escalations,
        "escalation_ok": len(escalate) - len(invented),
        "escalation_total": len(escalate),
        "answered_right": len(answered_right),
        "answer_total": len(answer),
    }


def print_report(cases: list[Case], threshold: float, misses_limit: int) -> None:
    hit, total = recall_at_3(cases)
    recall = hit / total if total else 0.0

    print("=" * 74)
    print("1. RECALL@3 — нужный фрагмент в первой тройке")
    print("=" * 74)
    verdict = "ЦЕЛЬ ДОСТИГНУТА" if recall >= TARGET_RECALL else "НИЖЕ ЦЕЛИ"
    print(f"  {hit} из {total} = {recall:.0%}   (цель ТЗ >= {TARGET_RECALL:.0%})  {verdict}")
    print("  от порога не зависит — это качество поиска\n")

    stats = score_at(cases, threshold)
    print("=" * 74)
    print(f"2. ЭСКАЛАЦИЯ при пороге {threshold}")
    print("=" * 74)
    print(
        f"  верно передано оператору: {stats['escalation_ok']} из "
        f"{stats['escalation_total']}"
    )
    invented = len(stats["invented"])
    mark = "ОК" if invented == 0 else "ПРОВАЛ — цель ТЗ ровно 0"
    print(f"  ВЫДУМАННЫХ ОТВЕТОВ:       {invented}   {mark}")
    print(
        f"  ложных эскалаций:         {len(stats['false_escalations'])} "
        f"(ответ есть и найден, но порог отсёк)"
    )
    print(
        f"  отвечено верно:           {stats['answered_right']} из "
        f"{stats['answer_total']}\n"
    )

    print("=" * 74)
    print("3. ПРОМАХИ")
    print("=" * 74)

    misses = []
    for case in cases:
        if case.expect == "answer" and not case.found:
            misses.append(("не найден", case))
    for case in stats["invented"]:
        misses.append(("ВЫДУМКА", case))
    for case in stats["false_escalations"]:
        misses.append(("ложная эскалация", case))

    if not misses:
        print("  промахов нет")
        return

    print(f"  {'тип':<18} {'score':>6}  вопрос")
    print("  " + "-" * 70)
    for kind, case in misses[:misses_limit]:
        print(f"  {kind:<18} {case.best_score:6.3f}  {case.question[:44]}")
        if kind == "не найден" and case.must_contain:
            print(
                f"  {'':<18} {'':>6}  ждали «{case.must_contain}», "
                f"нашли: {', '.join(case.top_titles[:2]) or '—'}"
            )
    if len(misses) > misses_limit:
        print(f"  … ещё {len(misses) - misses_limit}, см. --misses")


def print_sweep(cases: list[Case]) -> None:
    """Тот самый выбор порога, ради которого всё и затевалось."""
    print("\n" + "=" * 74)
    print("ПОРОГ: перебор диапазона 0,60–0,72 (раздел 3.2)")
    print("=" * 74)
    print(f"  {'порог':>6} {'выдумок':>8} {'ложн.эск.':>10} {'отвечено':>9}  вердикт")
    print("  " + "-" * 70)

    best = None
    for threshold in SWEEP:
        s = score_at(cases, threshold)
        invented = len(s["invented"])
        false_esc = len(s["false_escalations"])
        ok = invented == 0
        if ok and (best is None or s["answered_right"] > best[1]):
            best = (threshold, s["answered_right"])
        verdict = "годится" if ok else f"выдумывает ({invented})"
        print(
            f"  {threshold:6.2f} {invented:8} {false_esc:10} "
            f"{s['answered_right']:9}  {verdict}"
        )

    print()
    if best is None:
        print("  Ни один порог не даёт ноль выдумок. Поднимать выше 0,72 нельзя")
        print("  по ТЗ — значит, дело не в пороге: смотреть чанк и базу знаний.")
    else:
        print(f"  РЕКОМЕНДАЦИЯ: RAG_MIN_SCORE = {best[0]}")
        print(f"  ноль выдуманных ответов при {best[1]} верных ответах.")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", default=str(GOLDEN))
    parser.add_argument(
        "--threshold", type=float, default=settings.RAG_MIN_SCORE,
        help="порог для блока 2; по умолчанию из настроек",
    )
    parser.add_argument("--sweep", action="store_true", help="перебрать пороги")
    parser.add_argument(
        "--with-model", action="store_true",
        help="спрашивать модель на прошедших порог — видно второй фильтр",
    )
    parser.add_argument("--misses", type=int, default=15)
    parser.add_argument("--limit", type=int, help="взять первые N вопросов")
    args = parser.parse_args()

    cases = load_cases(Path(args.golden))
    if args.limit:
        cases = cases[: args.limit]

    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with AsyncSession(engine) as session:
            workspace = await session.scalar(
                select(Workspace).where(
                    Workspace.slug == settings.WORKSPACE_DEFAULT_SLUG
                )
            )
            if workspace is None:
                print(f"нет воркспейса {settings.WORKSPACE_DEFAULT_SLUG}")
                return 1

            chunks = await session.scalar(
                select(Chunk.id)
                .where(Chunk.workspace_id == workspace.id)
                .limit(1)
            )
            if chunks is None:
                print("база знаний пуста — сначала загрузите документы")
                return 1

            print(
                f"воркспейс {workspace.slug}, вопросов {len(cases)} "
                f"(answer {sum(1 for c in cases if c.expect == 'answer')}, "
                f"escalate {sum(1 for c in cases if c.expect == 'escalate')})\n"
            )
            started = time.monotonic()
            await run_cases(
                session,
                workspace.id,
                cases,
                with_model=args.with_model,
                threshold=args.threshold,
                bank_name=workspace.name,
            )
            elapsed = time.monotonic() - started
            print(
                f"прогон: {elapsed:.1f} сек, "
                f"{elapsed / max(len(cases), 1):.2f} сек на вопрос\n"
            )

            print_report(cases, args.threshold, args.misses)
            if args.sweep:
                print_sweep(cases)
    finally:
        await engine.dispose()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

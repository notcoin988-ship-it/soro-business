"""Счётная часть scripts/eval_rag.py (раздел 3.2 ТЗ).

Сам прогон golden set требует наполненной базы и живых эмбеддингов —
это ручной инструмент. А вот арифметика метрик и сравнение `must_contain`
с текстом фрагмента ошибаются молча: посчитает не то, а выглядеть будет
убедительно. Поэтому они проверяются здесь.

Скрипт лежит вне пакета `app`, поэтому подгружается по пути.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path("/code/scripts/eval_rag.py")
pytestmark = pytest.mark.skipif(
    not SCRIPT.exists(), reason="scripts/ не смонтирован (см. docker-compose.yml)"
)


def load_module():
    spec = importlib.util.spec_from_file_location("eval_rag", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Регистрация обязательна: у скрипта `from __future__ import
    # annotations`, и @dataclass резолвит строковые аннотации через
    # sys.modules[cls.__module__] — без этой строки Case падает на
    # AttributeError, причём в самом dataclasses.
    sys.modules["eval_rag"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ev():
    return load_module()


# ---------------------------------------------------------------------------
# сравнение must_contain с текстом фрагмента
# ---------------------------------------------------------------------------


def test_normalize_glues_digit_groups(ev):
    """«35 000» на сайте и «35000» в вопросе — одно число.

    Шапка golden.yaml прямо предупреждает: разойдутся разделители разрядов —
    получим ложные промахи и будем чинить поиск, который исправен.
    """
    assert ev.normalize("35 000 сомони") == ev.normalize("35000 сомони")


def test_normalize_handles_nbsp(ev):
    """Битрикс разделяет разряды неразрывным пробелом, глазом не отличить."""
    assert ev.normalize("35\xa0000") == ev.normalize("35000")


def test_normalize_unifies_decimal_separator(ev):
    """«15.5» и «15,5» — одна ставка."""
    assert ev.normalize("15.5%") == ev.normalize("15,5%")


def test_normalize_is_case_insensitive(ev):
    assert ev.normalize("Бессрочный") == ev.normalize("бессрочный")


def test_normalize_keeps_different_numbers_different(ev):
    """Сторож от чрезмерной терпимости: 15% и 155% путать нельзя."""
    assert ev.normalize("15%") != ev.normalize("155%")


# ---------------------------------------------------------------------------
# метрики
# ---------------------------------------------------------------------------


def case(ev, expect, *, found=False, score=0.0, must="x"):
    return ev.Case(
        question="в",
        expect=expect,
        must_contain=must if expect == "answer" else None,
        best_score=score,
        found=found,
    )


def test_recall_counts_only_answer_group(ev):
    """recall@3 считается по группе А. Вопросы на эскалацию в него не входят —
    у них и `must_contain` нет."""
    cases = [
        case(ev, "answer", found=True),
        case(ev, "answer", found=False),
        case(ev, "escalate"),
    ]
    assert ev.recall_at_3(cases) == (1, 2)


def test_invented_answer_is_counted(ev):
    """Ответа в базе нет, а близость выше порога — это выдумка.

    Цель ТЗ по ним — РОВНО НОЛЬ, поэтому считать их надо точно.
    """
    cases = [case(ev, "escalate", score=0.70)]
    assert len(ev.score_at(cases, 0.65)["invented"]) == 1
    assert len(ev.score_at(cases, 0.72)["invented"]) == 0


def test_false_escalation_needs_found_fragment(ev):
    """Ложная эскалация — это когда ответ есть, НАЙДЕН, но порог его отсёк.

    Если фрагмент вообще не найден, виноват поиск, а не порог, и в эту
    колонку такой случай попадать не должен — иначе, крутя порог, будем
    чинить не то.
    """
    found_but_cut = [case(ev, "answer", found=True, score=0.60)]
    not_found = [case(ev, "answer", found=False, score=0.60)]

    assert len(ev.score_at(found_but_cut, 0.65)["false_escalations"]) == 1
    assert len(ev.score_at(not_found, 0.65)["false_escalations"]) == 0


def test_answered_right_requires_both(ev):
    """Верный ответ = фрагмент найден И порог пройден."""
    cases = [
        case(ev, "answer", found=True, score=0.70),   # верно
        case(ev, "answer", found=True, score=0.50),   # отсёк порог
        case(ev, "answer", found=False, score=0.70),  # не найден
    ]
    assert ev.score_at(cases, 0.65)["answered_right"] == 1


def test_threshold_boundary_is_inclusive(ev):
    """Ровно на пороге — считается ответом, как и в core/rag.py (>=).

    Разъедься это на единицу в последнем знаке — метрики перестанут
    совпадать с поведением бота.
    """
    cases = [case(ev, "escalate", score=0.65)]
    assert len(ev.score_at(cases, 0.65)["invented"]) == 1


# ---------------------------------------------------------------------------
# чтение golden set
# ---------------------------------------------------------------------------


def test_golden_set_size_and_proportion(ev):
    """60 вопросов; по ТЗ 40 + 20, фактически 41 + 19.

    Расхождение осознанное: вопрос про вклад в евро стоял в группе «ответа
    нет», а ответ в документах есть — бот его честно процитировал, и это
    мы считали выдумкой. Ошибка была в наборе. Восстанавливать пропорцию
    выдуманным вопросом бессмысленно: набор ценен соответствием
    документам, а не арифметикой.
    """
    golden = Path("/code/tests/golden.yaml")
    if not golden.exists():
        pytest.skip("tests/ не смонтирован")

    cases = ev.load_cases(golden)
    assert len(cases) == 60
    assert sum(1 for c in cases if c.expect == "answer") == 41
    assert sum(1 for c in cases if c.expect == "escalate") == 19


def test_every_answer_case_has_must_contain(ev):
    """Без `must_contain` вопрос группы А не участвует в recall и тихо
    завышает метрику."""
    golden = Path("/code/tests/golden.yaml")
    if not golden.exists():
        pytest.skip("tests/ не смонтирован")

    for c in ev.load_cases(golden):
        if c.expect == "answer":
            assert c.must_contain, f"нет must_contain: {c.question}"


def test_unknown_expect_is_rejected(ev, tmp_path):
    """Опечатка в expect не должна молча выпасть из метрик."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "- question: вопрос\n  expect: maybe\n", encoding="utf-8"
    )
    with pytest.raises(ValueError):
        ev.load_cases(bad)

"""Оценка клиента: пять звёзд вместо пальца.

В DDL раздела 5 у `feedback.score` стоит `CHECK (score IN (-1, 1))` —
палец вверх или вниз. Прототип на экране 07 при этом обещает «4,4/5 по
380 оценкам», то есть пятибалльную шкалу. Противоречие внутри самого ТЗ;
решение — в пользу прототипа: именно его показывают банку, и «4,4 из 5»
на защите бюджета говорит больше, чем «82% довольных».

ЧТО ДЕЛАЕМ СО СТАРЫМИ СТРОКАМИ. Их немного (оценки появились неделю
назад), но выбрасывать данные клиентов нельзя. Палец вверх — это «всё
хорошо», палец вниз — «плохо»: 1 → 5, -1 → 1. Середины у старой шкалы не
было, поэтому и в новой её взяться неоткуда — это честнее, чем ставить
всем тройку.

ОБРАТНАЯ МИГРАЦИЯ схлопывает шкалу назад: 4-5 → 1, остальное → -1.
Данные при этом теряют точность и восстановить её нечем — так и должно
быть, downgrade здесь для аварийного отката, а не для регулярной работы.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-13
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


    # ДВА ИМЕНИ У ОДНОГО ОГРАНИЧЕНИЯ. В миграции 0001 CHECK записан прямо
    # в колонке, и имя ему придумал Postgres — `feedback_score_check`. В
    # модели SQLAlchemy то же ограничение названо `ck_feedback_score`, и
    # так оно будет называться в базе, созданной с нуля из метаданных.
    # Снимаем оба: миграция обязана отработать и на живом стенде, и на
    # свежей базе.


def upgrade() -> None:
    # Порядок обязателен: сначала снять старый CHECK, потом переписать
    # значения. Наоборот нельзя — UPDATE упрётся в ограничение.
    op.execute("ALTER TABLE feedback DROP CONSTRAINT IF EXISTS ck_feedback_score;")
    op.execute("ALTER TABLE feedback DROP CONSTRAINT IF EXISTS feedback_score_check;")
    op.execute("UPDATE feedback SET score = 5 WHERE score = 1;")
    op.execute("UPDATE feedback SET score = 1 WHERE score = -1;")
    op.execute(
        "ALTER TABLE feedback ADD CONSTRAINT ck_feedback_score "
        "CHECK (score BETWEEN 1 AND 5);"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE feedback DROP CONSTRAINT IF EXISTS ck_feedback_score;")
    op.execute("ALTER TABLE feedback DROP CONSTRAINT IF EXISTS feedback_score_check;")
    op.execute("UPDATE feedback SET score = 1 WHERE score >= 4;")
    op.execute("UPDATE feedback SET score = -1 WHERE score <> 1;")
    op.execute(
        "ALTER TABLE feedback ADD CONSTRAINT ck_feedback_score "
        "CHECK (score IN (-1, 1));"
    )

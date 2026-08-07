"""Индекс аудит-лога.

В DDL раздела 5 у `audit_log` нет ни одного индекса — при создании схемы
таблица была пустой, и это никого не смущало. Теперь в неё пишется каждый
вызов модели, а экран 07 «Аналитика» будет читать её по воркспейсу за
период. Без индекса это полный перебор растущей таблицы.

Порядок колонок именно такой: сначала равенство (`workspace_id`), потом
диапазон по времени. Обратный порядок для такого запроса бесполезен.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-07
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_audit_ws_time "
        "ON audit_log (workspace_id, created_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_audit_ws_time;")

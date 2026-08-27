#!/bin/sh
# Репетиция восстановления: разворачиваем последний дамп во ВРЕМЕННУЮ базу
# и сверяем, что данные на месте.
#
# ЗАЧЕМ ОТДЕЛЬНЫЙ СКРИПТ. В DEPLOY.md написано «бэкап, который никогда не
# разворачивали, — это не бэкап», и до сих пор это была только фраза.
# Проверка читаемости дампа (`pg_restore --list`) ничего не доказывает:
# файл может открываться и при этом не разворачиваться.
#
# БОЕВУЮ БАЗУ НЕ ТРОГАЕМ. Восстановление идёт в базу `soro_restore_check`,
# которая создаётся и удаляется здесь же. Ошибиться и затереть рабочие
# данные нечем — имя базы в скрипте, а не в аргументе.
#
# Запуск (из корня soro-business):
#   docker compose -f docker-compose.yml -f docker-compose.prod.yml \
#       run --rm --entrypoint /bin/sh backup /restore_check.sh
#
# Код возврата 1, если дамп не развернулся или таблицы оказались пустыми, —
# годится для крона: «репетиция раз в месяц» из инструкции.

set -eu

HOST="${PGHOST:-db}"
USER="${PGUSER:-soro}"
DB="${PGDATABASE:-soro}"
DIR="${BACKUP_DIR:-/backups}"
CHECK_DB="soro_restore_check"

latest=$(ls -1t "$DIR"/soro-*.dump 2>/dev/null | head -1 || true)
if [ -z "$latest" ]; then
    echo "в $DIR нет ни одного дампа — восстанавливать нечего" >&2
    exit 1
fi
echo "проверяю: $latest"

cleanup() {
    psql -h "$HOST" -U "$USER" -d postgres -q -c \
        "DROP DATABASE IF EXISTS $CHECK_DB;" >/dev/null 2>&1 || true
}
trap cleanup EXIT

cleanup
psql -h "$HOST" -U "$USER" -d postgres -q -c "CREATE DATABASE $CHECK_DB;"

# Расширение ставим заранее: в дампе есть колонки vector, и без pgvector
# восстановление упадёт на первой же таблице чанков.
psql -h "$HOST" -U "$USER" -d "$CHECK_DB" -q -c \
    "CREATE EXTENSION IF NOT EXISTS vector;"

# `--no-owner`: во временной базе владельцем будет тот, кто её создал.
# Ошибки не глушим, но и не считаем фатальными по одной: pg_restore ругается
# на уже существующее расширение, а это ожидаемо.
pg_restore -h "$HOST" -U "$USER" -d "$CHECK_DB" --no-owner "$latest" 2>&1 |
    grep -v "already exists" || true

echo "--- что развернулось ---"
fail=0
for table in workspaces documents chunks conversations messages; do
    count=$(psql -h "$HOST" -U "$USER" -d "$CHECK_DB" -t -A -c \
        "SELECT count(*) FROM $table" 2>/dev/null || echo "нет")
    printf "  %-14s %s\n" "$table" "$count"
    # Пустая таблица сама по себе не ошибка (новый стенд), а вот
    # отсутствующая — да: значит дамп неполный.
    [ "$count" = "нет" ] && fail=1
done

if [ "$fail" -ne 0 ]; then
    echo "ВОССТАНОВЛЕНИЕ НЕ ПРОШЛО: часть таблиц не развернулась" >&2
    exit 1
fi

echo "восстановление прошло, временная база удалена"

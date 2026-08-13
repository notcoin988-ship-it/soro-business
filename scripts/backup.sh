#!/bin/sh
# Бэкап базы: pg_dump в /backups, старые снимки удаляются.
#
# ПОЧЕМУ SHELL, А НЕ PYTHON. Внутри контейнера pgvector/pgvector нет ни
# приложения, ни его зависимостей — только сам Postgres и его утилиты.
# Ставить туда Python ради вызова pg_dump значит тащить в бэкап-контейнер
# половину образа бэкенда.
#
# ПОЧЕМУ НЕ CRON. В alpine-образе базы его нет, а ставить и настраивать
# демона ради одной задачи в сутки — лишняя движущаяся часть. Цикл со sleep
# делает то же и виден в `docker compose logs backup`.
#
# ФОРМАТ -Fc (custom): сжатый, восстанавливается выборочно (`pg_restore -t`)
# и не зависит от порядка объектов. Обычный SQL-дамп на базе с pgvector
# требует, чтобы расширение уже стояло, — custom это переживает.
#
# Запуск:
#   docker compose -f docker-compose.yml -f docker-compose.prod.yml \
#       exec backup /bin/sh /backup.sh          # снять снимок сейчас
#   ... exec backup /bin/sh /backup.sh --loop   # так его держит compose
#
# Восстановление (ВНИМАНИЕ: перезаписывает данные):
#   docker compose exec -T db pg_restore -U soro -d soro --clean --if-exists \
#       < backups/soro-2026-08-13-0300.dump

set -eu

HOST="${PGHOST:-db}"
USER="${PGUSER:-soro}"
DB="${PGDATABASE:-soro}"
DIR="${BACKUP_DIR:-/backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
# Раз в сутки. Меньше смысла нет: демо-стенд меняется медленно, а каждый
# дамп с эмбеддингами весит десятки мегабайт.
EVERY_SEC="${BACKUP_EVERY_SEC:-86400}"

snapshot() {
    mkdir -p "$DIR"
    name="$DIR/soro-$(date +%Y-%m-%d-%H%M).dump"

    # Пишем во временный файл и переименовываем: прерванный на середине
    # дамп не должен выглядеть как готовый бэкап.
    if pg_dump -h "$HOST" -U "$USER" -d "$DB" -Fc -f "$name.part"; then
        mv "$name.part" "$name"
        echo "бэкап готов: $name ($(du -h "$name" | cut -f1))"
    else
        rm -f "$name.part"
        echo "БЭКАП НЕ СНЯЛСЯ: pg_dump вернул ошибку" >&2
        return 1
    fi

    # Чистка старых. Считаем ПОСЛЕ успешного снимка: если дампы перестали
    # сниматься, старые должны остаться, а не выпасть по возрасту.
    deleted=$(find "$DIR" -name 'soro-*.dump' -mtime "+$KEEP_DAYS" -print -delete | wc -l)
    [ "$deleted" -gt 0 ] && echo "удалено старых снимков: $deleted"
    return 0
}

if [ "${1:-}" = "--loop" ]; then
    echo "бэкапы: каждые $EVERY_SEC с, хранение $KEEP_DAYS дней, каталог $DIR"
    while true; do
        # Ошибка одного снимка не должна убивать цикл: база могла быть
        # недоступна минуту, а следующий заход обычно проходит.
        snapshot || echo "повтор через $EVERY_SEC с"
        sleep "$EVERY_SEC"
    done
fi

snapshot

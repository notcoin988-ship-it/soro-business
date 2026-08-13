"""Файлы виджета: размер загрузчика и то, что бэкенд их отдаёт.

ПРО РАЗМЕР. Раздел 7.2 требует `loader.js` меньше 5 КБ — это скрипт,
который банк вставляет к себе в шаблон, и он грузится на каждой странице
сайта. Лимит легко пробить, не заметив: мобильная вёрстка добавила
полторы сотни строк, и файл вырос до 7 КБ. Тест ловит это раньше ревью.

Считаем сырой файл, а не gzip: на проде Caddy сжимает (в `encode zstd
gzip`), но лимит ТЗ записан без оговорок, и мерить его надо строго.
"""

from __future__ import annotations

import pytest

from app.main import WIDGET_DIR

# 5 КБ = 5120 байт. Берём именно двоичный килобайт: в ТЗ «КБ», а строже —
# значит безопаснее.
LIMIT_BYTES = 5 * 1024


def test_loader_fits_the_limit():
    size = (WIDGET_DIR / "loader.js").stat().st_size
    assert size <= LIMIT_BYTES, (
        f"loader.js вырос до {size} байт при лимите {LIMIT_BYTES} "
        "(раздел 7.2). Уберите комментарии в widget/README.md, "
        "а не поднимайте лимит."
    )


def test_loader_has_no_dependencies():
    """«Без зависимостей» из того же раздела: ни import, ни require, ни
    подгрузки чужих скриптов — иначе сайт банка тянет за нами хвост."""
    source = (WIDGET_DIR / "loader.js").read_text(encoding="utf-8")

    assert "import " not in source
    assert "require(" not in source
    assert "cdn." not in source


@pytest.mark.parametrize(
    "path", ["loader.js", "site.html", "demo.html", "frame/index.html", "frame/widget.js"]
)
def test_widget_files_are_on_disk(path):
    """Бэкенд отдаёт эти файлы по /w.js, /widget/site и /widget/frame/.
    Переименовали файл — узнать об этом лучше здесь, чем на встрече."""
    assert (WIDGET_DIR / path).is_file()


def test_frame_asks_the_backend_by_relative_path():
    """Фрейм ходит в API относительными путями: его отдаёт тот же бэкенд.
    Абсолютный адрес здесь означал бы, что на новом домене виджет молчит."""
    source = (WIDGET_DIR / "frame" / "widget.js").read_text(encoding="utf-8")

    assert '"/widget/stream' in source or "'/widget/stream" in source
    assert "http://localhost" not in source

"""Создание FastAPI-приложения (раздел 2.3 ТЗ).

Здесь собирается приложение: /health и роутеры каналов. Логики нет и не
должно быть — она в core/.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    analytics,
    channels,
    console,
    inbox,
    overview,
    playground,
    workspaces,
)
from app.channels import telegram, widget
from app.core import current
from app.config import settings

app = FastAPI(title="Soro Business Console", version="1.0.0")

@app.middleware("http")
async def pick_workspace(request, call_next):
    """Какой банк открыт в консоли — из заголовка `X-Workspace`.

    Ставится на каждый запрос и только на время запроса: подробности и
    причина, почему не параметр в каждой ручке, — в `core/current`.
    """
    current.set_slug(request.headers.get(current.HEADER))
    try:
        return await call_next(request)
    finally:
        current.set_slug(None)


app.include_router(console.router)
app.include_router(workspaces.router)
app.include_router(playground.router)
app.include_router(inbox.router)
app.include_router(analytics.router)
app.include_router(overview.router)
app.include_router(channels.router)
# Каналы подключаются по мере готовности. Telegram первый — раздел 7.1.
app.include_router(telegram.router)
app.include_router(widget.router)

# Файлы виджета: их отдаёт тот же бэкенд, что и API. В ТЗ сниппет банка
# ссылается на cdn.sorollm.tj/w.js, но CDN у демо-стенда нет и не будет —
# адрес берётся из PUBLIC_BASE_URL, он же ngrok на встрече.
#
# Каталог ищем в двух местах, потому что дерево внутри контейнера другое:
# там корень проекта — это `backend/`, смонтированный как `/code`, и
# `widget/` подмонтирован рядом (`/code/widget`). На хосте — на уровень
# выше, в корне репозитория. Один путь на оба случая не подобрать.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
WIDGET_DIR = next(
    (
        candidate
        for candidate in (_BACKEND_DIR / "widget", _BACKEND_DIR.parent / "widget")
        if candidate.is_dir()
    ),
    _BACKEND_DIR / "widget",
)

if WIDGET_DIR.is_dir():
    # Загрузчик лежит по короткому адресу: он попадает в шаблон сайта
    # банка, и чем короче строка, тем меньше шансов её переврать.
    @app.get("/w.js", include_in_schema=False)
    async def loader() -> FileResponse:
        return FileResponse(
            WIDGET_DIR / "loader.js",
            media_type="application/javascript",
            # Загрузчик меняется вместе с виджетом, а сайт банка кеширует
            # скрипты надолго. Пять минут — компромисс: демо переживает
            # правку, а сайт не ходит за файлом на каждый переход.
            headers={"Cache-Control": "public, max-age=300"},
        )

    # Страница-полигон: критерий готовности раздела 7 требует проверить
    # виджет «на тестовой странице», и она должна быть под рукой, а не
    # собираться заново перед каждой проверкой.
    @app.get("/widget/demo", include_in_schema=False)
    async def widget_demo() -> FileResponse:
        return FileResponse(WIDGET_DIR / "demo.html", media_type="text/html")

    # Две страницы намеренно. `/widget/demo` — технический полигон с
    # враждебными стилями: на нём проверяют, что чужой CSS не дотягивается
    # до виджета. `/widget/site` — то, что показывают заказчику: обычная
    # светлая страница банка, на которой виджет должен выглядеть уместно.
    @app.get("/widget/site", include_in_schema=False)
    async def widget_site() -> FileResponse:
        return FileResponse(WIDGET_DIR / "site.html", media_type="text/html")

    app.mount(
        "/widget/frame",
        StaticFiles(directory=WIDGET_DIR / "frame", html=True),
        name="widget-frame",
    )


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "workspace": settings.WORKSPACE_DEFAULT_SLUG,
        "model": settings.SORO_MODEL,
    }

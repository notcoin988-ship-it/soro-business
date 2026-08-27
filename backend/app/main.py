"""Создание FastAPI-приложения (раздел 2.3 ТЗ).

Здесь собирается приложение: роутеры, middleware воркспейса, подписчик
шины и статика виджета. Логики нет и не должно быть — она в core/.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    analytics,
    health,
    channels,
    console,
    inbox,
    overview,
    playground,
    reports,
    workspaces,
)
from app.channels import telegram, widget
from app.config import settings
from app.core import bus, current

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Подписчик шины живёт столько же, сколько приложение.

    Без него события доставляются только внутри своего процесса — этого
    хватает одному воркеру, но не двум (см. `core/bus`).
    """
    await bus.start()
    try:
        yield
    finally:
        await bus.stop()


app = FastAPI(title="Soro Business Console", version="1.0.0", lifespan=lifespan)

# Доступ с другого домена — только по явному списку.
#
# Нужен, когда консоль выложена отдельно от бэкенда: браузер иначе
# заблокирует и обычные запросы, и поток ответа (SSE). Список задаётся
# `CORS_ORIGINS` в `.env`; пусто — middleware не подключается вовсе, и
# поведение остаётся прежним.
#
# `allow_credentials` не включаем: консоль ходит с заголовком
# `X-Workspace`, а не с куками, и разрешать отправку кук чужому origin
# незачем.
_origins = [item.strip() for item in settings.CORS_ORIGINS.split(",") if item.strip()]
if _origins:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        # `ngrok-skip-browser-warning` — консоль шлёт его, чтобы туннель
        # отдал ответ API, а не HTML-заглушку. Не разрешить его здесь —
        # значит завалить preflight на каждом запросе.
        allow_headers=["Content-Type", "X-Workspace", "ngrok-skip-browser-warning"],
    )

@app.middleware("http")
async def pick_workspace(request, call_next):
    """Какой банк открыт в консоли — из заголовка `X-Workspace`.

    Ставится на каждый запрос и только на время запроса: подробности и
    причина, почему не параметр в каждой ручке, — в `core/current`.
    """
    # Заголовок — основной путь. Но поток ответа консоль открывает через
    # EventSource, а он заголовки слать не умеет вовсе: там воркспейс
    # приходит параметром `?ws=`, как это давно сделано в виджете
    # (`/widget/stream?ws=...`). Без этого площадка отвечала бы данными
    # банка по умолчанию, даже когда в шапке выбран другой — на показе
    # заказчику это худший из возможных сюрпризов.
    slug = request.headers.get(current.HEADER) or request.query_params.get("ws")
    current.set_slug(slug)
    try:
        return await call_next(request)
    finally:
        current.set_slug(None)


app.include_router(health.router)
app.include_router(console.router)
app.include_router(workspaces.router)
app.include_router(playground.router)
app.include_router(inbox.router)
app.include_router(analytics.router)
app.include_router(reports.router)
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
#
# ИЩЕМ ПО СОДЕРЖИМОМУ, А НЕ ПО НАЛИЧИЮ КАТАЛОГА. Первая версия брала
# первый существующий путь — и сломалась, как только бэкенд запустили в
# контейнере: маунт `./widget:/code/widget` вложен в маунт `./backend:/code`,
# и Docker создал на хосте ПУСТОЙ `backend/widget` как точку монтирования.
# После этого хостовые запуски (тесты, uvicorn) выбирали пустой каталог и
# падали на StaticFiles — все 600 тестов не собирались.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
WIDGET_DIR = next(
    (
        candidate
        for candidate in (_BACKEND_DIR / "widget", _BACKEND_DIR.parent / "widget")
        if (candidate / "loader.js").is_file()
    ),
    _BACKEND_DIR.parent / "widget",
)

# СОБРАННАЯ КОНСОЛЬ, ОТДАВАЕМАЯ ЭТИМ ЖЕ СЕРВЕРОМ.
#
# ЗАЧЕМ, если консоль выложена на GitHub Pages. Затем, что бесплатный
# туннель показывает браузеру страницу-предупреждение, и на ней нет
# CORS-заголовков: любой запрос выложенной консоли к API падает, а поток
# ответа (EventSource) не может даже отправить заголовок для её обхода.
# Cookie согласия здесь не спасает — для запросов с чужого домена браузер
# её не шлёт.
#
# Когда консоль отдаёт сам бэкенд, адрес один: CORS не нужен вовсе,
# предупреждение проходится один раз при открытии, поток работает.
# GitHub Pages остаётся витриной интерфейса и рабочим вариантом на
# постоянном адресе.
CONSOLE_DIR = next(
    (
        candidate
        for candidate in (
            _BACKEND_DIR / "console_dist",
            _BACKEND_DIR.parent / "console" / "dist",
        )
        if (candidate / "index.html").is_file()
    ),
    None,
)

if CONSOLE_DIR is not None:
    # `html=True` отдаёт index.html на неизвестные пути — консоль это
    # одностраничное приложение, и перезагрузка любого экрана должна
    # открывать её, а не 404.
    app.mount(
        "/console",
        StaticFiles(directory=CONSOLE_DIR, html=True),
        name="console",
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



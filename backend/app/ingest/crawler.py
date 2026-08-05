"""Обход сайта банка (раздел 6.2 ТЗ, ветка «Сайт»).

Жёстко заданные ТЗ лимиты — все в константах ниже:
обход в ширину от стартового URL · только тот же домен · максимум 150
страниц · глубина 3 · задержка 0,5 сек между запросами · уважать robots.txt.
Извлечение текста — в `parsers.parse_html` (main/article/body, без nav,
header, footer, script).

Чего в ТЗ нет и что решено здесь (каждый пункт — вопрос к Разрабу A,
продублирован в report.md недели 2):

1. **Дедупликация URL — и это ДВЕ разные операции.** `clean_url` приводит
   адрес к канонному виду, ничего не меняя для сервера: нижний регистр
   схемы и хоста, стандартный порт долой, якорь долой, параметры
   отсортированы, трекинговые (`utm_*`, `gclid`, `fbclid`, `_ga`)
   выброшены. По такому адресу можно ходить. `normalize_url` добавляет к
   этому срез хвостового слэша и годится **только для сравнения**: по
   ключу ходить нельзя, сайты с APPEND_SLASH отдают на него 404.
   Итог: `/tarify`, `/tarify/`, `/tarify#rates` и `/tarify?utm_source=fb`
   — одна страница, но запрашивается она ровно так, как написана на сайте.
2. **Редиректы на другой домен — обрываем.** Ответ по чужому домену не
   индексируется и ссылок из него не берём: `documents.source_url` должен
   остаться в пределах сайта банка. **Исключение — стартовый URL:** если
   переехал он сам, домен переопределяется по факту и обход продолжается.
   Найдено боевым прогоном: `eskhata.tj` редиректит на `eskhata.com`, и без
   исключения краулер умирал на первом запросе.
3. **PDF и прочие не-HTML на сайте — пропускаем, но собираем список.**
   Скачивать их в этой задаче нельзя: цепочка «скачать → сохранить в
   volume → создать documents» — это ingest, зона A. Найденные адреса
   отдаём в `CrawlResult.assets`, чтобы человек решил, грузить ли их
   руками через POST /api/documents.
4. **Таймауты и ретраи.** 10 сек на ответ (5 на соединение), 2 повтора на
   сетевую ошибку и 5xx с паузой 1 сек. 4xx не повторяем — это ответ, а не
   сбой.
5. **Кодировки.** Берём из заголовка Content-Type; если сервер её не
   прислал — смотрим `<meta charset>`; если и там нет — UTF-8 с заменой
   битых байт. Сайты банков РТ регулярно отдают windows-1251 без указания
   кодировки, и без этого таджикский текст превращается в мусор.
6. **Глубина.** Стартовая страница — глубина 0, `MAX_DEPTH=3` значит три
   уровня ссылок от старта включительно; со страниц глубины 3 ссылки уже
   не берём.
7. **Задержка и Crawl-delay.** Пауза перед каждым запросом кроме первого;
   если robots.txt просит больше 0,5 сек — слушаемся его.
8. **Лимит 150 считает УСПЕШНЫЕ ЗАПРОСЫ, а не записи в базу.** Пустые и
   не-HTML страницы тоже нагружают чужой сервер. Считать только то, что
   легло в базу, значит незаметно сделать по сайту втрое больше запросов,
   чем разрешено.
9. **`<base href>`** учитывается при разборе ссылок: без него относительные
   адреса собираются валидные, но не те.
10. **Дедупликация по содержимому, а не только по адресу.** Одна и та же
   страница часто доступна по нескольким адресам (сортировка, фильтры,
   сессия в query) — по URL это разные страницы. Текст сравнивается по
   sha1; копия в базу не идёт. Нагрузочный прогон без этой проверки давал
   20 дублей на 149 страниц.
11. **Слушаем, что страница говорит о себе.** `<link rel="canonical">` —
   настоящий адрес страницы (для CMS это штатный способ схлопнуть
   пагинацию); `<meta name="robots" content="noindex">` — не индексировать;
   `nofollow` (в мете или в самой ссылке) — не идти дальше. ТЗ требует
   уважать robots.txt, а это то же правило, записанное в другом месте.
12. **429 — не ошибка, а просьба притормозить.** Ждём `Retry-After`, но не
   больше минуты, и повторяем.
14. **TLS проверяется по умолчанию, отключается только явно.** Прогон по
   восьми банкам РТ показал: у трёх сайтов сертификат не проходит проверку
   (сервер не отдаёт промежуточный сертификат либо рвёт рукопожатие).
   Браузер такое прощает, Python — нет. Параметр `verify=False` существует,
   но включается руками, пишет предупреждение в лог задачи и требует
   решения тимлида: без проверки сертификата содержимое сайта можно
   подменить по дороге, а мы кладём его в базу знаний банка и выдаём
   клиентам как официальный ответ.
13. **Потолок на размер страницы — 5 МБ.** Всё, что больше, это не
   документ, а выгрузка: разбирать её значит потратить минуты процессора и
   засорить базу. Ограничение неполное — тело уже скачано; полноценная
   защита требует потокового чтения, вынесено в известные ограничения.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections import deque
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.ingest.parsers import parse_html

# --- лимиты ТЗ, менять только вместе с документом -------------------------
MAX_PAGES = 150
MAX_DEPTH = 3
DELAY_SEC = 0.5

# --- решения этой задачи --------------------------------------------------
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
RETRIES = 2
RETRY_PAUSE_SEC = 1.0
USER_AGENT = "SoroBusinessBot/1.0 (+knowledge base indexer)"

# Параметры, не влияющие на содержимое страницы: реклама и сессии.
# Сессионные особенно вредны — они плодят по новому URL на каждый заход.
TRACKING_PARAMS = (
    "gclid", "fbclid", "yclid", "_ga", "_openstat", "_ym_uid",
    "phpsessid", "jsessionid", "sessionid",
)
TRACKING_PREFIXES = ("utm_",)
# Потолок на размер страницы: HTML сверх этого — не документ, а выгрузка
MAX_BYTES = 5 * 1024 * 1024
# расширения, которые точно не HTML; PDF среди них — см. решение 3
ASSET_SUFFIXES = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".rtf", ".csv",
    ".zip", ".rar", ".7z", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
    ".ico", ".mp3", ".mp4", ".avi", ".apk", ".exe", ".css", ".js", ".xml",
)
META_CHARSET_RE = re.compile(
    rb"""<meta[^>]+charset=["']?\s*([\w-]+)""", re.I
)
PERCENT_RE = re.compile(r"%([0-9a-fA-F]{2})")


@dataclass(frozen=True)
class CrawledPage:
    """Одна проиндексированная страница сайта."""

    url: str
    title: str
    text: str
    depth: int


@dataclass
class CrawlStats:
    """Счётчики для лога сдачи: сколько страниц, где остановились, что отсекли."""

    # успешных ответов от сайта — ИМЕННО ЭТО считает лимит 150 страниц:
    # лимит про нагрузку на чужой сервер, а не про размер нашей базы
    fetched: int = 0
    indexed: int = 0            # из них страниц с непустым текстом
    skipped_robots: int = 0     # запрещено robots.txt
    skipped_offdomain: int = 0  # ссылка/редирект на чужой домен
    skipped_asset: int = 0      # не-HTML (см. assets)
    skipped_depth: int = 0      # страниц, дальше которых не пошли по глубине
    skipped_dup: int = 0        # уже видели после нормализации
    skipped_dup_text: int = 0   # другой адрес, но текст слово в слово тот же
    errors: int = 0             # не скачалось после всех ретраев
    stopped_reason: str = ""    # 'queue_empty' | 'max_pages'
    elapsed_sec: float = 0.0


@dataclass
class CrawlResult:
    pages: list[CrawledPage] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)  # найденные PDF и прочее
    stats: CrawlStats = field(default_factory=CrawlStats)
    log: list[str] = field(default_factory=list)     # человекочитаемый лог


# ---------------------------------------------------------------------------
# нормализация URL
# ---------------------------------------------------------------------------

DEFAULT_PORTS = {"http": "80", "https": "443"}


def clean_url(url: str) -> str:
    """Канонизация, которая НЕ меняет запрашиваемый ресурс.

    Срезаем якорь, приводим схему и хост к нижнему регистру, выбрасываем
    стандартный порт и трекинговые параметры, сортируем query. Всё это
    сервер видит одинаково.

    Хвостовой слэш НЕ трогаем — см. `normalize_url`.
    """
    url, _ = urldefrag(url.strip())
    # регистр процентного кодирования незначим (RFC 3986), но %d0%b0 и
    # %D0%B0 — разные строки, а значит и разные ключи дедупликации
    url = PERCENT_RE.sub(lambda m: "%" + m.group(1).upper(), url)
    parts = urlsplit(url)

    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    port = parts.port
    netloc = host
    if port and DEFAULT_PORTS.get(scheme) != str(port):
        netloc = f"{host}:{port}"

    query = "&".join(
        sorted(piece for piece in parts.query.split("&") if _keep_param(piece))
    )
    return urlunsplit((scheme, netloc, parts.path or "/", query, ""))


def _keep_param(piece: str) -> bool:
    """Оставить ли параметр запроса.

    Сравниваем ИМЯ параметра целиком, а не начало строки: проверка по
    префиксу выбрасывала бы `_gap` заодно с `_ga`.
    """
    if not piece:
        return False
    name = piece.split("=", 1)[0].lower()
    if name in TRACKING_PARAMS:
        return False
    return not name.startswith(TRACKING_PREFIXES)


def normalize_url(url: str) -> str:
    """Ключ дедупликации: `clean_url` плюс срез хвостового слэша.

    ВАЖНО: результат годится только для сравнения, запрашивать его нельзя.
    Боевой прогон по eskhata.com: `/events/esg/eco-otvetstvennyy/` отдаёт
    200, а тот же адрес без слэша — 404. Так ведут себя Битрикс, Django и
    вообще все, у кого включён APPEND_SLASH. Пока краулер ходил по
    нормализованным адресам, он сам себе сделал 160 «битых ссылок».

    Поэтому: сравниваем по ключу, а ходим по `clean_url`.
    """
    parts = urlsplit(clean_url(url))
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def same_domain(url: str, root: str) -> bool:
    """Тот же хост (и порт), что у стартового URL.

    Поддомены считаем чужими: `blog.eskhata.tj` — это другой сайт с другой
    вёрсткой, а лимит 150 страниц один на всех. Если тимлид решит иначе —
    правится здесь одной строкой.

    Сравниваем netloc уже нормализованных URL, поэтому `site` и `site:443`
    под https — одно и то же.
    """
    return urlsplit(normalize_url(url)).netloc == urlsplit(
        normalize_url(root)
    ).netloc


def looks_like_asset(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return path.endswith(ASSET_SUFFIXES)


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------


async def load_robots(client: httpx.AsyncClient, root: str) -> RobotFileParser:
    """Читает robots.txt через тот же httpx-клиент.

    Стандартный `RobotFileParser.read()` ходит в сеть сам, синхронно, мимо
    наших таймаутов и мимо тестового транспорта — поэтому только parse().
    Недоступный или битый robots.txt трактуем как «всё разрешено»: так же
    ведут себя поисковые краулеры при 404.
    """
    parser = RobotFileParser()
    parser.set_url(urljoin(root, "/robots.txt"))
    try:
        response = await client.get(urljoin(root, "/robots.txt"))
    except httpx.HTTPError:
        parser.parse([])
        return parser

    if response.status_code == 200:
        parser.parse(response.text.splitlines())
    else:
        parser.parse([])
    return parser


# ---------------------------------------------------------------------------
# обход
# ---------------------------------------------------------------------------


async def crawl(
    start_url: str,
    *,
    max_pages: int = MAX_PAGES,
    max_depth: int = MAX_DEPTH,
    delay: float = DELAY_SEC,
    verify: bool = True,
    client: httpx.AsyncClient | None = None,
) -> CrawlResult:
    """Обход в ширину от `start_url`. Возвращает страницы, лог и счётчики.

    `client` принимается снаружи, чтобы тесты подсовывали свой транспорт,
    а воркер переиспользовал один клиент на весь ingest.
    """
    result = CrawlResult()
    started = time.monotonic()

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            timeout=TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            verify=verify,
        )
    if not verify:
        # Проверка сертификата отключена — это осознанный риск, и он должен
        # быть виден в логе задачи, а не спрятан в конфиге. См. решение 14.
        result.log.append(
            "ВНИМАНИЕ: проверка TLS-сертификата отключена — "
            "содержимое сайта не защищено от подмены"
        )

    try:
        root = clean_url(start_url)
        robots = await load_robots(client, root)
        pause = max(delay, _crawl_delay(robots))
        if pause > delay:
            result.log.append(
                f"robots.txt просит Crawl-delay {pause} сек — слушаемся"
            )

        # в очереди лежат адреса для ЗАПРОСА (clean_url), а в множествах —
        # ключи сравнения (normalize_url). Путать их нельзя: по ключу
        # ходить в сеть нельзя, он может быть 404
        queue: deque[tuple[str, int]] = deque([(root, 0)])
        # seen — что уже поставлено в очередь (ключи ДО запроса)
        seen: set[str] = {normalize_url(root)}
        # processed — что уже разобрано (ключи адресов ПОСЛЕ редиректов)
        processed: set[str] = set()
        # хеш текста → глубина, на которой этот текст встретился впервые
        seen_text: dict[str, int] = {}
        first_request = True
        # для диагностики JS-сайтов: сколько разметки скачали и сколько
        # полезного текста из неё достали
        html_bytes = 0
        text_chars = 0

        while queue:
            # считаем запросы, а не проиндексированные страницы: пустые и
            # не-HTML тоже нагружают чужой сервер, и лимит ТЗ про них тоже
            if result.stats.fetched >= max_pages:
                result.stats.stopped_reason = "max_pages"
                result.log.append(
                    f"СТОП: набрали лимит {max_pages} страниц, "
                    f"в очереди осталось {len(queue)}"
                )
                break

            url, depth = queue.popleft()

            # адрес мог быть поставлен в очередь ссылкой, а потом оказаться
            # конечной точкой чужого редиректа — тогда он уже разобран, и
            # второй запрос за той же страницей не нужен
            if normalize_url(url) in processed:
                result.stats.skipped_dup += 1
                continue

            if not robots.can_fetch(USER_AGENT, url):
                result.stats.skipped_robots += 1
                result.log.append(f"[robots] {url}")
                continue

            if not first_request:
                await asyncio.sleep(pause)
            first_request = False

            response = await _fetch(client, url, result)
            if response is None and depth == 0 and not url.endswith("/"):
                # человек мог напечатать стартовый адрес без слэша, а сайту
                # слэш обязателен. Внутренние ссылки так не чиним: там адрес
                # написан самим сайтом, и вторая попытка — лишний запрос
                with_slash = url + "/"
                result.log.append(f"пробую со слэшем: {with_slash}")
                response = await _fetch(client, with_slash, result)
            if response is None:
                continue
            result.stats.fetched += 1

            final_url = clean_url(str(response.url))
            final_key = normalize_url(final_url)
            if depth == 0 and not same_domain(final_url, root):
                # Стартовая страница имеет право переехать: eskhata.tj
                # редиректит на eskhata.com, и обход по исходному правилу
                # умирал на первом же запросе. Домен переопределяем по
                # факту, robots.txt перечитываем — он у нового домена свой.
                result.log.append(f"стартовый URL переехал: {root} → {final_url}")
                root = final_url
                robots = await load_robots(client, root)
                pause = max(delay, _crawl_delay(robots))
                seen.add(final_key)
            if not same_domain(final_url, root):
                result.stats.skipped_offdomain += 1
                result.log.append(f"[редирект наружу] {url} → {response.url}")
                continue

            # Адрес после редиректов проверяем отдельно: /moved → /about
            # приводит нас на /about, который мог быть в очереди и сам по
            # себе. Без этой проверки та же страница скачается дважды и
            # ляжет в базу дублем.
            if final_key in processed:
                result.stats.skipped_dup += 1
                result.log.append(f"[дубль после редиректа] {url} → {final_url}")
                continue
            processed.add(final_key)
            seen.add(final_key)

            content_type = response.headers.get("content-type", "")
            if "html" not in content_type.lower():
                _add_asset(result, final_url)
                result.log.append(f"[не HTML: {content_type or '?'}] {url}")
                continue

            if len(response.content) > MAX_BYTES:
                # не документ, а выгрузка: разбирать её — минуты процессора
                # и десятки мегабайт в чанках
                result.stats.skipped_asset += 1
                result.log.append(
                    f"[слишком большая: {len(response.content) // 1024} КБ] {final_url}"
                )
                continue

            html = _decode(response)
            canonical, noindex, nofollow = page_directives(html, final_url)

            # canonical: сайт сам говорит, какой у страницы настоящий адрес.
            # Для Битрикса это штатный способ сказать «?PAGEN_1=3 — это всё
            # та же /events/», и он решает проблему пагинации системно.
            if canonical and same_domain(canonical, root):
                canon_key = normalize_url(canonical)
                if canon_key != final_key:
                    if canon_key in processed:
                        result.stats.skipped_dup += 1
                        result.log.append(
                            f"[canonical → уже есть] {final_url} → {canonical}"
                        )
                        continue
                    result.log.append(f"[canonical] {final_url} → {canonical}")
                    final_url, final_key = canonical, canon_key
                    processed.add(final_key)
                    seen.add(final_key)

            title, text = parse_html(html)
            html_bytes += len(response.content)
            text_chars += len(text)
            if not title:
                title = _title_from_url(final_url)
            if noindex:
                # robots.txt мы уважаем — значит и <meta name="robots">:
                # правило то же самое, просто записано в другом месте
                result.stats.skipped_robots += 1
                result.log.append(f"[noindex] {final_url}")
                text = ""

            if text:
                # Дедупликация по СОДЕРЖИМОМУ, а не только по адресу.
                # Нагрузочный прогон показал 20 копий на 149 страниц: сайты
                # отдают одну и ту же страницу по разным адресам (сортировка,
                # фильтры, сессия в query). По URL это разные страницы, и
                # без проверки текста в базу знаний лягут дубли — а дубли
                # фрагментов перекашивают выдачу поиска.
                digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
                first_depth = seen_text.get(digest)

                if first_depth is None:
                    seen_text[digest] = depth
                    result.stats.indexed += 1
                    result.pages.append(
                        CrawledPage(
                            url=final_url, title=title, text=text, depth=depth
                        )
                    )
                    result.log.append(
                        f"[{depth}] {final_url} — {len(text)} симв., «{title}»"
                    )
                else:
                    result.stats.skipped_dup_text += 1
                    result.log.append(f"[копия текста] {final_url}")
                    if depth >= first_depth:
                        # текст тот же, значит и ссылки те же, и собраны они
                        # были с не большей глубины — идти сюда незачем
                        continue
                    # а вот копия, найденная ВЫШЕ по дереву, даёт больший
                    # запас глубины: ссылки с неё собрать стоит
                    seen_text[digest] = depth
            else:
                result.log.append(f"[пусто] {final_url}")

            # ссылки со страницы предельной глубины уже не берём (решение 6)
            if depth >= max_depth:
                result.stats.skipped_depth += 1
                continue
            if nofollow:
                result.log.append(f"[nofollow] ссылки с {final_url} не берём")
                continue
            for link in _links(html, final_url, root, result):
                key = normalize_url(link)
                if key in seen:
                    result.stats.skipped_dup += 1
                    continue
                seen.add(key)
                # в очередь кладём адрес как он написан на сайте: слэш на
                # конце может быть обязательным
                queue.append((link, depth + 1))

        else:
            result.stats.stopped_reason = "queue_empty"
            result.log.append("СТОП: очередь пуста, сайт обойдён целиком")

    finally:
        if own_client:
            await client.aclose()

    result.stats.elapsed_sec = round(time.monotonic() - started, 2)
    _warn_if_javascript_site(result, html_bytes, text_chars)
    return result


# Признак — доля полезного текста в объёме HTML, а не длина страницы:
# короткая страница может быть просто короткой. Замер по живым сайтам:
#
#   humo.tj (главная)        146 КБ HTML →    33 символа = 0,02%
#   eskhata.com/depo/ozod/   189 КБ HTML →  2901 символ  = 1,54%
#   eskhata.com (главная)    360 КБ HTML →  7451 символ  = 2,07%
#   alif.tj                  535 КБ HTML → 23420 символов = 4,38%
#   amonatbonk.tj            149 КБ HTML →  6732 символа  = 4,50%
#
# Каркас без содержимого отличается от нормальной страницы на два порядка,
# так что 0,5% разделяет их с большим запасом в обе стороны.
JS_TEXT_RATIO = 0.005


def _warn_if_javascript_site(
    result: CrawlResult, html_bytes: int, text_chars: int
) -> None:
    """Предупредить, если сайт отдаёт пустой HTML и рисуется скриптами.

    Молчаливая пустая база знаний — худший исход из возможных: документ
    получит статус «Проиндексирован», на экране «База знаний» будет
    зелёная галочка, а бот начнёт эскалировать вообще всё. Пусть лучше
    человек увидит причину сразу.

    Стек зафиксирован ТЗ (httpx + selectolax), headless-браузера в нём
    нет и быть не может без решения тимлида, поэтому лечения тут нет —
    только честная диагностика.
    """
    if result.stats.fetched < 3 or html_bytes == 0:
        return
    ratio = text_chars / html_bytes
    if ratio >= JS_TEXT_RATIO:
        return
    result.log.append(
        f"ВНИМАНИЕ: полезного текста {ratio * 100:.2f}% от объёма HTML "
        f"({text_chars} символов на {html_bytes // 1024} КБ разметки). "
        f"Похоже, сайт формируется JavaScript'ом — краулер видит пустой "
        f"каркас. База знаний получится пустой; нужны документы банка или "
        f"его согласие на другой источник."
    )


async def _fetch(
    client: httpx.AsyncClient, url: str, result: CrawlResult
) -> httpx.Response | None:
    """GET с ретраями (решение 4). None — страницу пропускаем."""
    for attempt in range(RETRIES + 1):
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:
            if attempt == RETRIES:
                result.stats.errors += 1
                result.log.append(f"[сеть] {url}: {type(exc).__name__}")
                return None
            await asyncio.sleep(RETRY_PAUSE_SEC)
            continue

        if response.status_code == 429 and attempt < RETRIES:
            # «слишком часто» — не ошибка, а просьба притормозить. Ждём
            # столько, сколько попросил сервер (но не больше минуты:
            # Retry-After иногда приходит в часах)
            wait = _retry_after(response)
            result.log.append(f"[429] {url}: жду {wait:.0f} сек")
            await asyncio.sleep(wait)
            continue

        if response.status_code >= 500 and attempt < RETRIES:
            await asyncio.sleep(RETRY_PAUSE_SEC)
            continue
        if response.status_code >= 400:
            result.stats.errors += 1
            result.log.append(f"[HTTP {response.status_code}] {url}")
            return None
        return response
    return None


def page_directives(html: str, page_url: str) -> tuple[str | None, bool, bool]:
    """Что страница сама говорит о себе: (canonical, noindex, nofollow).

    * `<link rel="canonical">` — «настоящий» адрес страницы. Для CMS это
      штатный способ сказать «/events/?PAGEN_1=3 это всё та же /events/».
      Уважать его — самый честный способ не плодить дубли в базе знаний.
    * `<meta name="robots" content="noindex">` — страницу просят не
      индексировать. robots.txt мы уважаем, значит и это тоже: правило одно
      и то же, просто записано в другом месте.
    * `nofollow` в том же теге — не идти по ссылкам с этой страницы.
    """
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)

    canonical = None
    node = tree.css_first('link[rel="canonical"]')
    if node is not None:
        href = (node.attributes.get("href") or "").strip()
        if href:
            canonical = clean_url(urljoin(page_url, href))

    noindex = nofollow = False
    for meta in tree.css('meta[name="robots"], meta[name="Robots"]'):
        content = (meta.attributes.get("content") or "").lower()
        noindex = noindex or "noindex" in content
        nofollow = nofollow or "nofollow" in content

    return canonical, noindex, nofollow


def _links(html: str, page_url: str, root: str, result: CrawlResult) -> list[str]:
    """Ссылки со страницы: свой домен, не ассеты, нормализованные.

    Принимаем уже декодированный html, а не Response: страницу и так
    разбирает `parse_html`, и декодировать её второй раз незачем.

    Разбираем ИСХОДНЫЙ html, а не очищенный: ТЗ велит выкидывать nav и
    footer из текста, но именно там лежит меню сайта. Выкинь их до сбора
    ссылок — и краулер увидит только первую страницу.
    """
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)

    # <base href> переопределяет базу для всех относительных ссылок
    # страницы. Встречается редко, но если его не учесть, ссылки
    # разъезжаются молча: адреса собираются валидные, только не те.
    base = tree.css_first("base[href]")
    if base is not None:
        page_url = urljoin(page_url, (base.attributes.get("href") or "").strip())

    out: list[str] = []
    for node in tree.css("a[href]"):
        href = (node.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        # rel="nofollow" — сайт просит по этой ссылке не ходить
        if "nofollow" in (node.attributes.get("rel") or "").lower():
            continue

        absolute = clean_url(urljoin(page_url, href))
        if urlsplit(absolute).scheme not in ("http", "https"):
            continue
        if not same_domain(absolute, root):
            result.stats.skipped_offdomain += 1
            continue
        if looks_like_asset(absolute):
            _add_asset(result, absolute)
            continue
        out.append(absolute)
    return out


def _add_asset(result: CrawlResult, url: str) -> None:
    """Не-HTML: считаем и складываем в список без дублей.

    Ассет попадает сюда двумя путями — по расширению ссылки и по
    Content-Type ответа, — и один и тот же файл легко учесть дважды.
    """
    result.stats.skipped_asset += 1
    if url not in result.assets:
        result.assets.append(url)


MAX_RETRY_AFTER_SEC = 60.0


def _retry_after(response: httpx.Response) -> float:
    raw = response.headers.get("retry-after", "")
    try:
        value = float(raw)
    except ValueError:
        # формат с датой встречается редко; ждём столько же, сколько после 5xx
        return RETRY_PAUSE_SEC
    return min(max(value, 0.0), MAX_RETRY_AFTER_SEC)


def _decode(response: httpx.Response) -> str:
    """Текст ответа с честной кодировкой (решение 5)."""
    if response.charset_encoding:
        return response.text

    match = META_CHARSET_RE.search(response.content[:4096])
    if match:
        encoding = match.group(1).decode("ascii", "ignore")
        try:
            return response.content.decode(encoding, errors="replace")
        except LookupError:
            pass
    return response.content.decode("utf-8", errors="replace")


def _crawl_delay(robots: RobotFileParser) -> float:
    try:
        value = robots.crawl_delay(USER_AGENT)
    except Exception:
        return 0.0
    return float(value) if value else 0.0


def _title_from_url(url: str) -> str:
    """Запасной заголовок: последний сегмент пути (решение из parse_html)."""
    tail = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    return tail or urlsplit(url).hostname or url


def format_report(result: CrawlResult) -> str:
    """Готовый текст для лога сдачи — то, что показываем тимлиду."""
    s = result.stats
    return "\n".join(
        [
            *result.log,
            "",
            f"успешных запросов:      {s.fetched}  (это и есть лимит страниц)",
            f"страниц с текстом:      {s.indexed}",
            f"максимальная глубина:   {max((p.depth for p in result.pages), default=0)}",
            f"отсечено robots.txt:    {s.skipped_robots}",
            f"чужой домен:            {s.skipped_offdomain}",
            f"не-HTML (см. assets):   {s.skipped_asset}",
            f"дубли URL:              {s.skipped_dup}",
            f"копии по тексту:        {s.skipped_dup_text}",
            f"ошибок загрузки:        {s.errors}",
            f"остановка:              {s.stopped_reason}",
            f"время:                  {s.elapsed_sec} сек",
        ]
    )

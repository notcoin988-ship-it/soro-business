# Перенос стенда на другую машину

Этот файл описывает, как поднять Soro Business на чистом ноутбуке. Он
нужен потому, что репозиторий — это только код: данные стенда и секреты
в git не лежат и переезжают отдельно.

## Чего в репозитории нет

| Что | Где взять | Почему не в git |
|---|---|---|
| `.env` | у тимлида, флешкой или зашифрованным архивом | боевые ключи Soro и Telegram. Правило 10.1: секрет в git — инцидент, и приватность репозитория этого не меняет |
| `soro-db.dump` | ассет релиза `transfer-2026-08-27` | 7 МБ бинарника, который меняется каждый день — история распухнет без пользы |
| `uploads.tgz` | ассет того же релиза | 56 МБ документов банков. Клиентским PDF в репозитории не место |
| `docker-compose.override.yml` | ассет того же релиза | настройки конкретной машины: урезанный батч эмбеддингов, порт 5433, выключенный переранкер |
| веса `bge-m3` | скачаются сами при первом старте | 2,3 ГБ, тянутся из Hugging Face |

## Что установить

| Что | Версия | Зачем |
|---|---|---|
| Docker Desktop + WSL2 | текущая | весь стек |
| Node.js | **20.20.2** (см. `.node-version`) | сборка консоли и `npm run deploy` |
| git | любая | этот репозиторий |
| ngrok | текущая, плюс `ngrok config add-authtoken <токен>` | туннель для показа |
| gh CLI | по желанию | публикация консоли на GitHub Pages |

Python на хост ставить не нужно: бэкенд собирается в образе
`python:3.11-slim`, tesseract с языками `tgk+rus` уже внутри.

**Память.** На машине с 16 ГБ и меньше положите `~/.wslconfig`, иначе WSL
умирает целиком на прогреве эмбеддингов — все контейнеры разом выходят с
кодом 255, и в их логах при этом пусто:

```ini
[wsl2]
memory=11GB
swap=4GB
```

Диска нужно около 10 ГБ: образы, веса модели, база.

## Порядок установки

```bash
git clone https://github.com/notcoin988-ship-it/soro-business.git
cd soro-business
```

Положить рядом `.env` и `docker-compose.override.yml`, скачать из релиза
`soro-db.dump` и `uploads.tgz`.

### 1. Поднять стек — поэтапно, не одной командой

Одной командой WSL валится: TEI на прогреве забирает около 5,6 ГБ, и если
в этот момент стартует всё остальное, виртуальная машина умирает целиком.

```bash
docker compose up -d db redis
docker compose up -d embeddings
curl localhost:8080/health          # ждать 200, около минуты
docker compose up -d backend worker
```

### 2. Вернуть данные

```bash
# база
docker compose cp soro-db.dump db:/tmp/soro-db.dump
docker compose exec -T db pg_restore -U soro -d soro --clean --if-exists /tmp/soro-db.dump

# загруженные документы
docker run --rm -v soro-business_uploads:/v -v "$PWD:/in" alpine \
  tar xzf /in/uploads.tgz -C /v
```

Проверка: `curl localhost:8000/api/overview` отдаёт JSON с цифрами.

### 3. Туннель и адреса

```bash
ngrok http 8000
```

Адрес меняется при каждом запуске, поэтому после старта:

1. вписать новый адрес в `PUBLIC_BASE_URL` в `.env` — иначе на экране
   «Каналы» будет мёртвый скрипт виджета;
2. `docker compose up -d --force-recreate --no-deps backend`;
3. переставить вебхук Telegram на новый адрес:

```bash
curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -d "url=https://<новый-адрес>/webhooks/telegram" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

### 4. Консоль

Два рабочих способа показа:

* `https://<туннель>/console/` — бэкенд отдаёт консоль сам. Пересобрать с
  `VITE_BASE=/console/`, результат кладётся в `console/dist`, она
  смонтирована в контейнер.
* `https://notcoin988-ship-it.github.io/soro-console/?api=https://<туннель>`
  — фронт на GitHub Pages, репозиторий `soro-console`. Требует
  `CORS_ORIGINS=https://notcoin988-ship-it.github.io` в `.env` (уже стоит).

Через cloudflared показывать нельзя: SSE-поток не доходит, ответ модели не
появляется вообще, и выглядит это как «модель сломалась». Только ngrok.

## Грабли этой сборки

* **Порт 5432 занят.** Если на машине стоит свой PostgreSQL, он
  перехватывает подключения с хоста, и клиент молча попадает в чужую базу.
  В `docker-compose.override.yml` порт вынесен на 5433.
* **Переранкер выключен** в том же override — на 16 ГБ его не хватает.
  На машине с большей памятью удалите блок и поднимите `reranker`.
* **Модель.** `SORO_API_URL` в `.env` указывает на прямой IP: домен
  `soro.zehnlab.ai` не отвечал на 19.08.2026. Проверяйте оба адреса перед
  показом.
* **Первый запуск ngrok в браузере** показывает страницу-предупреждение.
  Для варианта с `/console/` её надо один раз пройти до встречи.

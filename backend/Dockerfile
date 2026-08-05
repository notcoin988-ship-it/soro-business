# Python 3.11 — ровно 3.11, как требует раздел 3.1 ТЗ
FROM python:3.11-slim

# tesseract-ocr-tgk нужен для OCR-ветки парсера PDF (раздел 6.2):
# страница короче 40 символов считается сканом и гонится через pytesseract
# с lang='tgk+rus'
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-tgk \
        tesseract-ocr-rus \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code

# пакет app копируем до установки: setuptools ищет его при `pip install -e .`
COPY pyproject.toml ./
COPY app ./app
# [dev] — pytest и pytest-asyncio: тесты гоняются внутри контейнера, потому
# что OCR-ветке нужен tesseract с языками tgk+rus, а на Windows его нет
RUN pip install --no-cache-dir -e ".[dev]"

COPY . .

# каталог загрузок из раздела 6.1: /data/uploads/{workspace}/{uuid}.{ext}
RUN mkdir -p /data/uploads

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

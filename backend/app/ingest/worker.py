"""RQ-воркер индексации (разделы 6.1 и 6.3 ТЗ).

ОТВЕТСТВЕННОСТЬ: задача `ingest_document(document_id)` — единственное
место, где документ превращается в строки таблицы `chunks`.

КОНВЕЙЕР:

 1. `documents.status = 'indexing'`;
 2. `ingest.parsers.parse_file` (файл) или `ingest.crawler.crawl` (сайт);
 3. `ingest.chunker.chunk_pages` — фрагменты с шапкой и перехлёстом;
 4. эмбеддинги: POST `EMBEDDINGS_URL/embed` батчами по 32;
 5. запись `chunks` вместе с `tsv = to_tsvector('simple', lower(text))`;
 6. после последнего фрагмента `status='ready'`, `indexed_at=now()`;
 7. любая ошибка → `status='failed'`, текст в `documents.error`.

ПРОГРЕСС: `chunks_done`/`chunks_total` в `documents.settings` — этого поля
нет в DDL раздела 5, оно добавлено нами (report.md, п. 1), но требуется
экраном 02.

НЕРЕШЁННЫЙ ВОПРОС: `chunks.page` объявлен `INT`, а для сайта туда нужен
«URL-хвост» (report.md, п. 12). Предлагаемый вариант — по строке
`documents` на каждую страницу сайта, `page = NULL`, URL в `source_url`.

ЗАВИСИМОСТИ: parsers, crawler, chunker, models, Redis.
СТАТУС: не реализовано. Ближайшая задача — без него база пустая.
"""

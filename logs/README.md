# Логи прогонов

Сырые выводы команд, на которых построены выводы в `report.md`. Держим их
в репозитории, потому что критерии сдачи требуют «показать лог», а не
пересказ: тимлид должен видеть цифры своими глазами.

## Неделя 2

| Файл | Что это | Чем снят |
|---|---|---|
| `week2-pytest.txt` | полный прогон тестов: 181 passed, 26 xfailed | `docker compose exec backend python -m pytest app/tests -q` |
| `week2-crawl-full.txt` | **полные лимиты ТЗ**: 150 страниц, глубина 3, пауза 0,5 сек — 149 страниц с текстом, 123 содержательных | `docker compose exec backend python scripts/crawl_site.py https://eskhata.tj/` |
| `week2-stress.txt` | нагрузочный прогон: 1000-страничный полигон, 6 инвариантов | `docker compose exec backend python scripts/stress_crawl.py` |
| `week2-crawl-many.txt` | восемь банков РТ по 30 страниц: у трёх сломан TLS | `docker compose exec backend python scripts/crawl_many.py` |
| `week2-crawl-eskhata.txt` | ранний обход на 25 страниц (до правки со слэшем) | `... scripts/crawl_site.py https://eskhata.tj/ --max-pages 25` |
| `week2-crawl-eskhata-full.txt` | тот же ранний обход, полный лог построчно | тот же запуск с `--out` |
| `week2-bench-index.txt` | замер индексации: плотный 40-страничный PDF и скан | `docker compose exec backend python scripts/bench_index.py --pages 40 --density 14` и `--pages 5 --scan` |
| `week2-real-pdf.txt` | парсер на четырёх настоящих PDF банка, 6 страниц через OCR | `docker compose exec backend python scripts/check_real_pdf.py` |

Разбор с выводами и вопросами тимлиду — `../report.md`, пункты 10–19.
Разбор самого кода (что как устроено и почему) — `../WALKTHROUGH-week2.md`.

**Важно при повторе:** тесты гонять с остановленным контейнером
эмбеддингов (`docker compose stop embeddings`) — TEI держит ~10 ГБ из 11,
отведённых WSL, и одновременный прогон выбивает контейнеры по OOM
(`ExitCode 137`). Для `bench_index.py` эмбеддинги, наоборот, нужны.

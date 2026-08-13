"""Нагрузочный прогон: сколько клиентов стенд держит и с какой задержкой.

ЗАЧЕМ. На пилоте банк спросит «а если напишут сто человек сразу». Отвечать
на это надо цифрой, а не «должно хватить». Скрипт гоняет через виджет
столько одновременных диалогов, сколько скажут, и печатает перцентили —
медиану, 95-й и худший ответ.

ЧТО ИМЕННО МЕРЯЕТ. Полный путь клиента: POST сообщения, ожидание ответа в
SSE-потоке, разбор `final`. То есть поиск по базе, вызов модели и запись в
базу — всё, что делает бот на живой вопрос. Отдельно мерить эндпоинты
смысла нет: узкое место всегда в поиске или в модели.

  docker compose exec backend python scripts/loadtest.py
  docker compose exec backend python scripts/loadtest.py --users 50 --rounds 3

ЧЕСТНОЕ ПРЕДУПРЕЖДЕНИЕ. На CPU-стенде эмбеддинги считаются 3–4 секунды, и
это дно, ниже которого не опуститься никакой параллельностью: TEI считает
батчи по очереди. Цифры этого прогона имеет смысл сравнивать только с
цифрами такого же прогона на другой машине, а не с нормативом «6 секунд»
из раздела 3 — тот про одиночный вопрос на незагруженном стенде.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import uuid

import httpx

BASE = "http://127.0.0.1:8000"

# Вопрос из golden.yaml: он есть в базе знаний, то есть проходит весь путь
# целиком — поиск, модель, запись. Вопрос без ответа мерил бы только поиск.
QUESTION = "Мӯҳлати пасандози мӯҳлатнок чанд рӯз аст?"

# Дольше минуты ждать нечего: на живом демо клиент к этому времени уже
# закрыл вкладку, и такой ответ всё равно считается провалом.
TIMEOUT_SEC = 60


async def one_client(client: httpx.AsyncClient, number: int) -> tuple[float, str]:
    """Один клиент: открыть поток, спросить, дождаться ответа."""
    uid = f"load-{number}-{uuid.uuid4().hex[:8]}"
    started = time.monotonic()

    try:
        async with client.stream(
            "GET", f"/widget/stream?uid={uid}", timeout=TIMEOUT_SEC
        ) as stream:
            # Поток открыт — теперь можно спрашивать: событие, посланное
            # раньше подписки, никто не услышит.
            await client.post(
                "/widget/messages", json={"uid": uid, "text": QUESTION}
            )

            event = ""
            async for line in stream.aiter_lines():
                if line.startswith("event: "):
                    event = line[len("event: ") :]
                elif line.startswith("data: ") and event == "final":
                    data = json.loads(line[len("data: ") :])
                    spent = time.monotonic() - started
                    return spent, "ok" if data.get("text") else "пусто"
    except Exception as exc:  # noqa: BLE001 — нагрузочный прогон не падает
        return time.monotonic() - started, type(exc).__name__

    return time.monotonic() - started, "поток кончился без final"


async def round_of(users: int) -> list[tuple[float, str]]:
    limits = httpx.Limits(max_connections=users * 2)
    async with httpx.AsyncClient(base_url=BASE, limits=limits) as client:
        return await asyncio.gather(*(one_client(client, i) for i in range(users)))


def report(times: list[float], outcomes: list[str]) -> None:
    ok = outcomes.count("ok")
    times = sorted(times)

    def percentile(share: float) -> float:
        return times[min(int(len(times) * share), len(times) - 1)]

    print(f"  успешных     : {ok} из {len(outcomes)}")
    print(f"  медиана      : {statistics.median(times):.1f} с")
    print(f"  95-й перц.   : {percentile(0.95):.1f} с")
    print(f"  худший       : {times[-1]:.1f} с")

    bad = [outcome for outcome in outcomes if outcome != "ok"]
    if bad:
        kinds = {kind: bad.count(kind) for kind in set(bad)}
        print(f"  сбои         : {kinds}")


async def main(args) -> int:
    print(f"нагрузка: {args.users} клиентов × {args.rounds} заходов, {BASE}")
    all_times: list[float] = []
    all_outcomes: list[str] = []

    for number in range(1, args.rounds + 1):
        started = time.monotonic()
        results = await round_of(args.users)
        times = [spent for spent, _ in results]
        outcomes = [outcome for _, outcome in results]
        all_times += times
        all_outcomes += outcomes

        print(f"\nзаход {number} — {time.monotonic() - started:.1f} с на всех:")
        report(times, outcomes)

    print("\nитого:")
    report(all_times, all_outcomes)

    # Код возврата по доле успешных: прогон, где каждый десятый клиент не
    # получил ответа, — это провал, а не «в целом неплохо».
    failed = all_outcomes.count("ok") < len(all_outcomes)
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Нагрузочный прогон стенда")
    parser.add_argument("--users", type=int, default=10, help="одновременных клиентов")
    parser.add_argument("--rounds", type=int, default=1, help="сколько заходов")
    raise SystemExit(asyncio.run(main(parser.parse_args())))

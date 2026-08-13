"""Сценарий приёмки (раздел 6.1 задачи недели 6) — восемь шагов подряд.

ЗАЧЕМ СКРИПТ, А НЕ ЧЕК-ЛИСТ В ГОЛОВЕ. Критерий сдачи — «три прогона
подряд без сбоев». Руками это полтора часа кликов на прогон, и на третьем
человек перестаёт замечать, что телеметрия пустая, а порог не сработал.
Здесь прогон — одна команда, а результат каждого шага печатается вместе с
ФАКТИЧЕСКИМИ числами: не «ок», а «4 фрагмента, лучший 0,71, 3,9 с».

    docker compose exec backend python scripts/acceptance.py
    docker compose exec backend python scripts/acceptance.py --with-index
    docker compose exec backend python scripts/acceptance.py --site https://eskhata.com

ЧТО СКРИПТ НЕ ПРОВЕРЯЕТ И ПОЧЕМУ:

* шаг 4 (QR → Telegram с телефона) требует человека с телефоном. Скрипт
  проверяет то, что можно: что вебхук прописан на живой адрес и Telegram
  не копит ошибки доставки. Остальное печатается как ручной шаг;
* шаг 6 (WhatsApp) не реализован — канал отложен решением. Шаг честно
  помечается пропущенным, а не «ок»: приёмка не должна выглядеть зелёной
  там, где кода нет.

ШАГ 1 ВЫКЛЮЧЕН ПО УМОЛЧАНИЮ (`--with-index`). Он загружает документы, а
это меняет базу знаний стенда: на демо это делают один раз при подготовке,
а не перед каждым прогоном. Замер времени индексации остаётся тем же.

ВОЗВРАЩАЕТ 1, если хоть один автоматический шаг провалился, — чтобы три
прогона подряд можно было запустить в цикле и увидеть срыв, а не
пролистать его глазами.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from sqlalchemy import func, select

sys.path.insert(0, "/code")

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import AuditLog, Conversation, Message  # noqa: E402

BASE = "http://127.0.0.1:8000"

# Вопросы приёмки. Первый обязан найтись в базе, второй — обязан НЕ
# найтись: «почему у меня списали 90 сомони» это вопрос про личный счёт,
# и правильный ответ на него — эскалация, а не выдумка.
#
# ПОЧЕМУ НЕ «ОЯНДАСОЗ», как в прототипе и в сценарии экрана 04: вклада с
# таким именем у банка нет, его выдумал прототип (в базе 0 фрагментов с
# этим словом, замер лежит в коммите экрана 03). Вопрос про него на
# приёмке показал бы эскалацию вместо ответа — и виноват был бы не бот.
# Взят вопрос из `tests/golden.yaml`: таджикский, про срочный вклад,
# лучший фрагмент 0,64 при пороге 0,60.
QUESTION_ANSWERABLE = "Мӯҳлати пасандози мӯҳлатнок чанд рӯз аст?"
QUESTION_PERSONAL = "Почему у меня списали 90 сомони вчера вечером?"

# Телефон для шага 8: он должен уехать в базу замаскированным.
PHONE = "+992 93 123 45 67"

# Норматив раздела 3: полный ответ быстрее шести секунд.
ANSWER_BUDGET_SEC = 6.0

# Норматив шага 1: два PDF и сайт индексируются за пять минут.
INDEX_BUDGET_SEC = 5 * 60

PDF_DIR = Path("/code/app/tests/data")

# Телеграм-пользователь для проверки склейки без телефона. Номер заведомо
# не существует: настоящие id короче и не начинаются с девятки подряд.
FAKE_TG_USER = 900000001


@dataclass
class Step:
    number: int
    title: str
    state: str = "—"  # ок | СБОЙ | пропущен | вручную
    facts: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return self.state == "СБОЙ"


def sse_events(chunk: str) -> list[tuple[str, dict]]:
    """Разобрать накопленный SSE-текст в пары (событие, данные)."""
    events = []
    for frame in chunk.split("\n\n"):
        if not frame.strip() or frame.startswith(":"):
            continue
        name, data = "", ""
        for line in frame.splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        if name and data:
            events.append((name, json.loads(data)))
    return events


async def ask_playground(client: httpx.AsyncClient, question: str) -> dict:
    """Задать вопрос на «Площадке» и собрать поток целиком."""
    started = time.monotonic()
    posted = await client.post("/api/playground/messages", json={"text": question})
    posted.raise_for_status()
    message_id = posted.json()["message_id"]

    body = ""
    async with client.stream(
        "GET", f"/api/playground/stream?message_id={message_id}", timeout=120
    ) as response:
        async for piece in response.aiter_text():
            body += piece

    events = dict(sse_events(body))
    events["_seconds"] = round(time.monotonic() - started, 1)
    return events


# ---------------------------------------------------------------------------
# шаги
# ---------------------------------------------------------------------------


async def step_1_index(client: httpx.AsyncClient, site: str | None) -> Step:
    step = Step(1, "Индексация: 2 PDF + сайт за ≤ 5 минут")

    files = sorted(PDF_DIR.glob("*.pdf"))[:2]
    if not files:
        step.state = "СБОЙ"
        step.facts.append(f"в {PDF_DIR} нет ни одного PDF")
        return step

    started = time.monotonic()
    ids = []
    for path in files:
        response = await client.post(
            "/api/documents",
            files={"file": (path.name, path.read_bytes(), "application/pdf")},
        )
        response.raise_for_status()
        ids.append(response.json()["id"])
    if site:
        response = await client.post("/api/documents", json={"url": site})
        response.raise_for_status()
        ids.append(response.json()["id"])

    while time.monotonic() - started < INDEX_BUDGET_SEC:
        documents = (await client.get("/api/documents")).json()
        mine = [d for d in documents if d["id"] in ids]
        if all(d["status"] == "ready" for d in mine):
            break
        if any(d["status"] == "failed" for d in mine):
            step.state = "СБОЙ"
            step.facts.append(
                "; ".join(f"{d['title']}: {d['error']}" for d in mine if d["error"])
            )
            return step
        await asyncio.sleep(3)

    spent = round(time.monotonic() - started)
    documents = (await client.get("/api/documents")).json()
    mine = [d for d in documents if d["id"] in ids]
    ready = [d for d in mine if d["status"] == "ready"]
    chunks = sum(d["chunks"] for d in ready)

    step.facts.append(f"{len(ready)} из {len(mine)} готовы за {spent} с, {chunks} фрагментов")
    if len(ready) != len(mine):
        step.state = "СБОЙ"
        step.facts.append(
            "не все документы дошли до «Проиндексирован» — проверьте, поднят ли worker"
        )
    elif spent > INDEX_BUDGET_SEC:
        step.state = "СБОЙ"
        step.facts.append(f"норматив шага — {INDEX_BUDGET_SEC} с")
    else:
        step.state = "ок"
    return step


async def step_2_playground(client: httpx.AsyncClient) -> Step:
    step = Step(2, "Площадка: вопрос на таджикском, стрим, фрагменты, < 6 с")
    events = await ask_playground(client, QUESTION_ANSWERABLE)

    if "error" in events:
        step.state = "СБОЙ"
        step.facts.append(f"модель недоступна: {events['error'].get('message')}")
        return step

    retrieval = events.get("retrieval", {})
    final = events.get("final", {})
    fragments = retrieval.get("fragments", [])
    telemetry = final.get("telemetry", {})
    seconds = events["_seconds"]

    step.facts.append(
        f"{len(fragments)} фрагментов, лучший {retrieval.get('best_score')}, "
        f"порог {retrieval.get('min_score')}"
    )
    step.facts.append(
        f"поиск {telemetry.get('search_ms')} мс, генерация "
        f"{telemetry.get('generation_ms')} мс, всего {seconds} с"
    )

    problems = []
    if len(fragments) < 3:
        problems.append("фрагментов меньше трёх")
    if not final.get("text"):
        problems.append("ответ пустой")
    if not re.search(r"\[\d+\]", final.get("text", "")):
        problems.append("в ответе нет ссылок [1]")
    if not final.get("chunks_used"):
        problems.append("chunks_used пуст")
    if not telemetry.get("generation_ms"):
        problems.append("телеметрия генерации пуста")
    if seconds > ANSWER_BUDGET_SEC:
        problems.append(f"дольше норматива {ANSWER_BUDGET_SEC} с")

    step.state = "СБОЙ" if problems else "ок"
    step.facts.extend(problems)
    return step


async def step_3_escalation(client: httpx.AsyncClient) -> Step:
    step = Step(3, "Личный вопрос: бот не выдумывает, диалог в инбоксе")

    before = (await client.get("/api/inbox/counters")).json()
    # Через канал виджета, а не «Площадку»: карточка в инбоксе появляется
    # только у настоящего диалога, площадка намеренно в базу не пишет.
    uid = f"acceptance-{int(time.time())}"
    await client.post(
        "/widget/messages", json={"uid": uid, "text": QUESTION_PERSONAL}
    )

    reply = ""
    for _ in range(40):
        await asyncio.sleep(1)
        after = (await client.get("/api/inbox/counters")).json()
        if after["waiting"] > before["waiting"]:
            queue = (await client.get("/api/inbox?status=waiting")).json()
            card = next((c for c in queue if c["preview"].startswith("Почему")), None)
            if card:
                reply = card["reason"]
                step.facts.append(
                    f"эскалация в очереди, причина {card['reason']}, "
                    f"ожидают {after['waiting']}"
                )
            break
    else:
        step.state = "СБОЙ"
        step.facts.append("диалог не появился в очереди оператора за 40 с")
        return step

    async with SessionLocal() as session:
        answer = await session.scalar(
            select(Message)
            .where(Message.role == "assistant")
            .order_by(Message.id.desc())
        )
    numbers = re.findall(r"\d+[\.,]?\d*", answer.text if answer else "")
    step.facts.append(f"ответ бота: {(answer.text if answer else '')[:70]}…")
    if numbers:
        step.state = "СБОЙ"
        step.facts.append(f"в ответе на личный вопрос есть числа: {numbers}")
    else:
        step.state = "ок" if reply else "СБОЙ"
    return step


async def step_4_telegram(client: httpx.AsyncClient) -> Step:
    step = Step(4, "QR → Telegram отвечает с тем же источником")
    step.state = "вручную"

    if not settings.TELEGRAM_BOT_TOKEN:
        step.facts.append("TELEGRAM_BOT_TOKEN не задан — канал не поднят")
        step.state = "СБОЙ"
        return step

    from app.channels.telegram import get_bot

    bot = get_bot()
    try:
        info = await bot.get_webhook_info()
    finally:
        # Своя HTTP-сессия aiogram живёт до конца процесса и на выходе
        # ругается «Unclosed client session», подменяя код возврата. Для
        # скрипта, который зовут в цикле три раза, это критично: код
        # возврата — единственный признак «прогон сорвался».
        await bot.session.close()
    step.facts.append(f"вебхук: {info.url or 'не задан'}")
    step.facts.append(f"в очереди {info.pending_update_count}")
    if info.last_error_message:
        step.state = "СБОЙ"
        step.facts.append(f"последняя ошибка доставки: {info.last_error_message}")
    elif not info.url:
        step.state = "СБОЙ"
        step.facts.append("вебхук не прописан — QR откроет бота, который молчит")
    else:
        step.facts.append(
            f"отсканируйте QR на экране 05 и спросите: «{QUESTION_ANSWERABLE}»"
        )
    return step


async def step_5_widget_to_telegram(client: httpx.AsyncClient) -> Step:
    step = Step(5, "Виджет → «Продолжить в Telegram»: один диалог, два канала")
    uid = f"acceptance-link-{int(time.time())}"

    await client.post(
        "/widget/messages", json={"uid": uid, "text": f"Мой номер {PHONE}"}
    )
    await asyncio.sleep(2)
    await client.post("/widget/messages", json={"uid": uid, "text": QUESTION_ANSWERABLE})
    await asyncio.sleep(3)

    token = (await client.post("/widget/link-token", json={"uid": uid})).json()
    step.facts.append(f"токен выдан, ссылка {token['url'][:48]}…")

    # Тот же путь, которым пойдёт живой клиент, только апдейт присылаем
    # сами: телефона у скрипта нет, а проверить склейку надо.
    update = {
        "update_id": int(time.time()),
        "message": {
            "message_id": 1,
            "date": int(time.time()),
            "chat": {"id": FAKE_TG_USER, "type": "private"},
            "from": {"id": FAKE_TG_USER, "is_bot": False, "first_name": "Приёмка"},
            "text": f"/start {token['token']}",
        },
    }
    response = await client.post(
        "/webhooks/telegram",
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": settings.TELEGRAM_WEBHOOK_SECRET},
    )
    step.facts.append(f"вебхук ответил {response.status_code}")

    card = (await client.get("/api/omni/latest")).json()
    channels = card.get("channels", [])
    step.facts.append(f"каналы последнего диалога: {', '.join(channels) or '—'}")

    async with SessionLocal() as session:
        masked = await session.scalar(
            select(Message.text_masked)
            .where(Message.text.contains("Мой номер"))
            .order_by(Message.id.desc())
        )

    problems = []
    if set(channels) < {"telegram", "widget"}:
        problems.append("после склейки в диалоге не два канала")
    if masked and "[PHONE]" not in masked:
        problems.append(f"телефон не замаскирован: {masked}")
    step.facts.append(f"маска: {masked}")

    step.state = "СБОЙ" if problems else "ок"
    step.facts.extend(problems)
    return step


async def step_6_whatsapp() -> Step:
    step = Step(6, "WhatsApp: эскалация, ответ оператора, «вернуть боту»")
    step.state = "пропущен"
    step.facts.append("канал не реализован (channels/whatsapp.py — заглушка)")
    step.facts.append("в .env нет WHATSAPP_TOKEN и WHATSAPP_PHONE_ID")
    return step


async def step_7_analytics(client: httpx.AsyncClient, before: dict) -> Step:
    step = Step(7, "Аналитика сходится с только что сделанным")
    after = (await client.get("/api/analytics?days=1")).json()

    grew = {
        "диалогов": after["conversations"]["total"] - before["conversations"]["total"],
        "к оператору": after["conversations"]["by_operator"]
        - before["conversations"]["by_operator"],
        "нет ответа в базе": after["attention"]["no_answer"]
        - before["attention"]["no_answer"],
    }
    step.facts.append(", ".join(f"{name} +{value}" for name, value in grew.items()))
    step.facts.append(
        f"медиана ответа {after['median_latency_ms']} мс, "
        f"каналов в разрезе: {len(after['channels'])}"
    )

    if grew["диалогов"] < 1 or grew["к оператору"] < 1:
        step.state = "СБОЙ"
        step.facts.append("экран не увидел действий прогона")
    else:
        step.state = "ок"
    return step


async def step_8_audit(started_at: float) -> Step:
    step = Step(8, "audit_log и маскирование ПДн")
    async with SessionLocal() as session:
        calls = (
            await session.scalars(
                select(AuditLog)
                .where(AuditLog.event == "llm_call")
                .order_by(AuditLog.id.desc())
                .limit(20)
            )
        ).all()
        raw_phones = await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.text_masked.contains("992 93 123"))
        )

    fresh = [c for c in calls if c.created_at.timestamp() > started_at]
    with_chunks = [c for c in fresh if c.payload.get("chunk_ids")]
    with_latency = [c for c in fresh if c.payload.get("latency_ms")]

    step.facts.append(
        f"llm_call за прогон: {len(fresh)}, из них с chunk_ids "
        f"{len(with_chunks)}, с латентностью {len(with_latency)}"
    )
    step.facts.append(f"незамаскированных телефонов в text_masked: {raw_phones}")

    problems = []
    if not fresh:
        problems.append("ни одного вызова модели не записано")
    if fresh and not with_latency:
        problems.append("латентность в payload пустая")
    if raw_phones:
        problems.append("телефон утёк в text_masked")

    step.state = "СБОЙ" if problems else "ок"
    step.facts.extend(problems)
    return step


# ---------------------------------------------------------------------------


async def run(args) -> int:
    started_at = time.time()
    global QUESTION_ANSWERABLE
    if args.question:
        QUESTION_ANSWERABLE = args.question
    async with httpx.AsyncClient(base_url=args.base, timeout=60) as client:
        try:
            await client.get("/health")
        except httpx.HTTPError as exc:
            print(f"бэкенд по адресу {args.base} не отвечает: {exc}")
            return 1

        before = (await client.get("/api/analytics?days=1")).json()

        steps = []
        if args.with_index:
            steps.append(await step_1_index(client, args.site))
        else:
            skipped = Step(1, "Индексация: 2 PDF + сайт за ≤ 5 минут", "пропущен")
            skipped.facts.append("запустите с --with-index (меняет базу знаний стенда)")
            steps.append(skipped)

        steps.append(await step_2_playground(client))
        steps.append(await step_3_escalation(client))
        steps.append(await step_4_telegram(client))
        steps.append(await step_5_widget_to_telegram(client))
        steps.append(await step_6_whatsapp())
        steps.append(await step_7_analytics(client, before))
        steps.append(await step_8_audit(started_at))

    print()
    for step in steps:
        mark = {"ок": "✓", "СБОЙ": "✗", "пропущен": "⏭", "вручную": "☝"}[step.state]
        print(f"{mark} шаг {step.number}. {step.title}")
        for fact in step.facts:
            print(f"      {fact}")
    print()

    failed = [step for step in steps if step.failed]
    if failed:
        print(f"ПРОГОН НЕ ПРОЙДЕН: сбоев {len(failed)}")
        return 1
    print("Прогон пройден. Шаги 4 и 6 закрываются руками — см. вывод выше.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Сценарий приёмки, раздел 6.1")
    parser.add_argument("--base", default=BASE, help="адрес бэкенда")
    parser.add_argument(
        "--with-index",
        action="store_true",
        help="выполнить шаг 1: загрузить документы и дождаться индексации",
    )
    parser.add_argument("--site", help="ссылка на сайт банка для шага 1")
    parser.add_argument(
        "--question",
        help="вопрос шага 2; по умолчанию таджикский вопрос про вклад из golden.yaml",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

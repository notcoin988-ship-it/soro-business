"""Аудит-лог обращений (таблица `audit_log`, раздел 5 ТЗ).

ОТВЕТСТВЕННОСТЬ: оставить след о каждом вызове модели, каждой эскалации
и каждом добавленном документе. Пункт 8 сценария приёмки проверяет это
глазами: «в audit_log есть записи обо всех вызовах модели с chunk_ids и
латентностью».

СОБЫТИЯ — те, что перечислены в комментарии DDL:
  `llm_call`   — вопрос ушёл в модель: чем ответили и на чём;
  `escalation` — диалог передан оператору и почему;
  `doc_add`    — в базу знаний добавлен документ.

ДВА ПРАВИЛА, КОТОРЫЕ НЕЛЬЗЯ НАРУШАТЬ:

1. В `payload` кладём только МАСКИРОВАННЫЙ текст. Аудит хранится три
   года; складывать туда номера карт — ровно то, от чего защищает
   раздел 8.2. Вызывающий обязан передать уже замаскированное.
2. Падение аудита не роняет ответ клиенту. Клиент ждёт ответа и не
   виноват в том, что у нас проблемы с записью следа. Ошибку глушим и
   пишем в лог приложения — так же, как `escalation.notify` не роняет
   диалог из-за оборванного сокета оператора.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import policy
from app.models import AuditLog, Workspace

log = logging.getLogger(__name__)

EVENT_LLM_CALL = "llm_call"
EVENT_ESCALATION = "escalation"
EVENT_DOC_ADD = "doc_add"
# Сверх списка DDL: кто и за какой период выгружал цифры банка (экран 08 и
# та же просьба в Telegram). Событий три в комментарии схемы, но отчёт —
# ровно тот случай, о котором через год спросят «кто это смотрел».
EVENT_REPORT = "report"


async def record(
    session: AsyncSession,
    workspace: Workspace,
    event: str,
    payload: dict,
) -> None:
    """Записать событие, если аудит включён в контуре безопасности."""
    if not policy.enabled(workspace, policy.AUDIT_LOG):
        return

    try:
        session.add(
            AuditLog(workspace_id=workspace.id, event=event, payload=payload)
        )
        await session.flush()
    except Exception as exc:  # noqa: BLE001 — см. правило 2 в шапке модуля
        log.warning("не удалось записать в audit_log (%s): %s", event, exc)

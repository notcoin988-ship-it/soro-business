"""Аналитика — экран 07 (приложение Б ТЗ).

ОТВЕТСТВЕННОСТЬ: отдать цифры приложения Б за последние `days` суток и
ничего больше.

  GET /api/analytics?days=7

САМИ ЗАПРОСЫ ПЕРЕЕХАЛИ В `core/reports.py`. Причина не в красоте: экран 08
отвечает на «нужен отчёт за июнь» теми же цифрами за произвольный период, и
если запросы жили бы в двух местах, экран 07 и отчёт за те же дни однажды
показали бы разные числа. Считать в Python то, что умеет считать SQL,
по-прежнему нельзя: цифры на защите бюджета банка должны быть
воспроизводимы запросом, который можно показать их же аналитику.

Здесь остались только потолок на `days` и форма ответа.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import reports
from app.core.dialog import get_workspace
from app.db import get_session

router = APIRouter(prefix="/api", tags=["analytics"])

# Дольше трёх месяцев смотреть незачем: демо-стенду две недели, а на
# проде такой запрос без индекса по времени положит базу. Отчёт словами
# (экран 08) заглядывает дальше — там период спрашивают штучно, а не на
# каждое открытие экрана; его потолок — `reports.MAX_RANGE_DAYS`.
MAX_DAYS = 90


@router.get("/analytics")
async def analytics(
    days: int = Query(7, ge=1, le=MAX_DAYS),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Цифры экрана 07 за последние `days` суток."""
    workspace = await get_workspace(session)
    period = reports.rolling_period(days)
    data = await reports.collect(session, workspace.id, period)
    return {"days": days, **data}

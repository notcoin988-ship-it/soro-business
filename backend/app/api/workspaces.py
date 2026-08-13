"""Воркспейсы — список банков и заведение нового.

  GET  /api/workspaces   список с числом документов и диалогов
  POST /api/workspaces   завести банк {slug, name}

ЗАЧЕМ. Раздел 1.1 ТЗ обещает изолированное пространство на каждый банк:
свои документы, свои каналы, свой аудит-лог. В схеме это есть с первого
дня — `workspace_id` стоит в каждой таблице, — а завести второй банк было
нечем: slug брался из `.env`. Кнопка «Добавить банк» в шапке консоли
закрывает эту дыру.

ЧТО ПРОИСХОДИТ ПОСЛЕ СОЗДАНИЯ. Ничего волшебного: новый банк пустой.
Документы грузят на экране 02, каналы подключают на экране 05 — то же
самое, что делали для первого. Иллюзию «всё готово» здесь создавать
нельзя, поэтому и в ответе честно едут нули.

ПЕРЕКЛЮЧЕНИЕ живёт не здесь: консоль присылает выбранный slug заголовком
`X-Workspace`, а разбирает его middleware (см. `core/current`).
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import Conversation, Document, Workspace

router = APIRouter(prefix="/api", tags=["workspaces"])

# Slug уходит в адреса, в сниппет виджета (`data-ws`) и в имя папки с
# загруженными файлами. Поэтому — только латиница, цифры и дефис.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")


class WorkspaceIn(BaseModel):
    slug: str
    name: str


@router.get("/workspaces")
async def list_workspaces(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """Все банки стенда. Порядок — по времени заведения, первый сверху."""
    rows = (
        await session.execute(
            select(
                Workspace.id,
                Workspace.slug,
                Workspace.name,
                select(func.count())
                .select_from(Document)
                .where(Document.workspace_id == Workspace.id)
                .scalar_subquery()
                .label("documents"),
                select(func.count())
                .select_from(Conversation)
                .where(Conversation.workspace_id == Workspace.id)
                .scalar_subquery()
                .label("conversations"),
            ).order_by(Workspace.id)
        )
    ).all()

    return [
        {
            "slug": row.slug,
            "name": row.name,
            "documents": row.documents,
            "conversations": row.conversations,
            # Тот, что в `.env`: к нему уходят каналы, пока им не сказали
            # иначе, и оператору полезно видеть, какой из банков главный.
            "default": row.slug == settings.WORKSPACE_DEFAULT_SLUG,
        }
        for row in rows
    ]


@router.post("/workspaces", status_code=201)
async def create_workspace(
    payload: WorkspaceIn, session: AsyncSession = Depends(get_session)
) -> dict:
    """Завести банк. Slug менять потом нельзя — он уезжает в сниппеты."""
    slug = payload.slug.strip().lower()
    name = payload.name.strip()

    if not SLUG_RE.match(slug):
        raise HTTPException(
            status_code=422,
            detail="slug: латиница, цифры и дефис, от 3 до 40 символов",
        )
    if not name:
        raise HTTPException(status_code=422, detail="у банка должно быть название")

    exists = await session.scalar(select(Workspace).where(Workspace.slug == slug))
    if exists is not None:
        raise HTTPException(status_code=409, detail=f"банк {slug} уже заведён")

    workspace = Workspace(slug=slug, name=name)
    session.add(workspace)
    await session.commit()

    return {
        "slug": workspace.slug,
        "name": workspace.name,
        "documents": 0,
        "conversations": 0,
        "default": False,
    }

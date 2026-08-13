"""Список банков и заведение нового (`/api/workspaces`).

Раздел 1.1 обещает банку изолированное пространство: свои документы, свои
каналы, свой аудит-лог. В схеме это было с первого дня, а завести второй
банк было нечем — slug брался из `.env`. Здесь проверяется и заведение, и
изоляция: данные одного банка не должны появляться у другого.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core import current, dialog
from app.db import get_session
from app.main import app
from app.models import Workspace


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield AsyncClient(transport=ASGITransport(app=app), base_url="http://console")
    app.dependency_overrides.clear()
    current.set_slug(None)


async def test_new_bank_starts_empty(client, session):
    """Заведённый банк пустой, и в ответе это видно.

    Иллюзию «всё готово» создавать нельзя: документы грузят на экране 02,
    каналы подключают на экране 05 — так же, как для первого банка.
    """
    async with client:
        response = await client.post(
            "/api/workspaces", json={"slug": "bank-demo", "name": "Демо-банк"}
        )

    assert response.status_code == 201
    assert response.json() == {
        "slug": "bank-demo",
        "name": "Демо-банк",
        "documents": 0,
        "conversations": 0,
        "default": False,
    }
    assert await session.scalar(
        select(Workspace).where(Workspace.slug == "bank-demo")
    )


@pytest.mark.parametrize(
    "slug",
    ["Банк", "AB", "с пробелом", "ВЕРХНИЙ", "точка.точка", "-минус", "a" * 45],
)
async def test_bad_slug_is_rejected(client, slug):
    """Slug уезжает в адреса, в сниппет виджета (`data-ws`) и в имя папки
    с файлами — там кириллице и пробелам не место."""
    async with client:
        response = await client.post(
            "/api/workspaces", json={"slug": slug, "name": "Банк"}
        )

    assert response.status_code == 422


async def test_duplicate_slug_is_a_conflict(client):
    async with client:
        await client.post(
            "/api/workspaces", json={"slug": "bank-twice", "name": "Первый"}
        )
        second = await client.post(
            "/api/workspaces", json={"slug": "bank-twice", "name": "Второй"}
        )

    assert second.status_code == 409


async def test_nameless_bank_is_rejected(client):
    async with client:
        response = await client.post(
            "/api/workspaces", json={"slug": "bank-noname", "name": "   "}
        )

    assert response.status_code == 422


async def test_list_counts_documents_and_dialogs(client, session, workspace):
    """В списке видно, где что лежит: пустой банк от рабочего отличается
    именно этими цифрами."""
    identity = await dialog.resolve_identity(
        session, workspace.id, "widget", "ws-list-1"
    )
    await dialog.resolve_conversation(session, workspace.id, identity.contact_id)

    async with client:
        rows = (await client.get("/api/workspaces")).json()

    mine = next(row for row in rows if row["slug"] == workspace.slug)
    assert mine["conversations"] == 1


async def test_header_switches_the_workspace(client, session):
    """Заголовок `X-Workspace` решает, чей банк обслуживает запрос.

    Это и есть переключение в консоли: без него все экраны показывали бы
    воркспейс из `.env`, какой бы банк оператор ни выбрал.
    """
    async with client:
        await client.post(
            "/api/workspaces", json={"slug": "bank-header", "name": "Банк заголовка"}
        )
        info = (
            await client.get(
                "/api/workspace", headers={current.HEADER: "bank-header"}
            )
        ).json()

    assert info["slug"] == "bank-header"
    assert info["name"] == "Банк заголовка"


async def test_documents_of_another_bank_are_invisible(client, session, workspace):
    """Изоляция из раздела 1.1: документ одного банка не виден другому."""
    async with client:
        await client.post(
            "/api/workspaces", json={"slug": "bank-empty", "name": "Пустой"}
        )
        documents = (
            await client.get(
                "/api/documents", headers={current.HEADER: "bank-empty"}
            )
        ).json()

    assert documents == []

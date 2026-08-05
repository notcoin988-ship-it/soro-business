"""Общие фикстуры тестов.

Тесты ядра работают с настоящим PostgreSQL из compose, а не с заглушкой:
половина логики здесь — это SQL (уникальность идентичности, поиск
открытого диалога, каскады), и на моках она не проверяется.

Чтобы прогон не оставлял мусора, каждый тест идёт внутри транзакции,
которая в конце откатывается. `join_transaction_mode="create_savepoint"`
нужен потому, что код ядра сам делает `session.commit()` — без этого
режима первый же commit закрыл бы внешнюю транзакцию, и откатывать стало
бы нечего.
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models import Workspace


@pytest_asyncio.fixture
async def db_engine():
    """Свой движок на каждый тест.

    Общий `app.db.engine` держит пул соединений, привязанных к тому циклу
    событий, в котором они открылись. pytest-asyncio даёт каждому тесту
    свой цикл — и со второго теста соединения из пула оказываются чужими:
    «Event loop is closed». NullPool не кеширует соединения, поэтому такой
    ситуации не возникает.
    """
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session(db_engine):
    """Сессия в транзакции, которая откатится после теста."""
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        async_session = AsyncSession(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        try:
            yield async_session
        finally:
            await async_session.close()
            await transaction.rollback()


@pytest_asyncio.fixture
async def demo_workspace(session) -> Workspace:
    """Воркспейс из `.env` — с ним работают API и ядро по умолчанию.

    Ищем существующий: slug уникален, а в базе разработки этот воркспейс
    уже есть. Создаём только если базы коснулись впервые.
    """
    from sqlalchemy import select

    from app.config import settings

    workspace = await session.scalar(
        select(Workspace).where(Workspace.slug == settings.WORKSPACE_DEFAULT_SLUG)
    )
    if workspace is None:
        workspace = Workspace(
            slug=settings.WORKSPACE_DEFAULT_SLUG, name="Банк Эсхата"
        )
        session.add(workspace)
        await session.flush()
    return workspace


@pytest_asyncio.fixture
async def workspace(session) -> Workspace:
    """Свой воркспейс на каждый тест — чтобы тесты не видели данные друг друга."""
    workspace = Workspace(slug="test-ws", name="Тестовый банк")
    session.add(workspace)
    await session.flush()
    return workspace

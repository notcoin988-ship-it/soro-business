"""Контур безопасности и аудит-лог (экран 01, пункт 8 приёмки).

Четыре переключателя, которые банк показывает своей службе безопасности.
Проверяется главное: они действительно что-то делают, значения по
умолчанию в пользу защиты, а выключение аудита не ломает ответ клиенту.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core import audit, dialog, policy
from app.db import get_session
from app.main import app
from app.models import AuditLog, Message

WS = "test-ws"
CARD = "5058123456789012"


@pytest.fixture(autouse=True)
async def restore_demo_settings(session, demo_workspace):
    """Вернуть настройки демо-воркспейса после теста.

    Эндпоинты сами делают commit, и запись переживает откат тестовой
    транзакции: база общая с разработкой, и выключенный в тесте флаг
    оставался выключенным на живом стенде. Ловилось так — после прогона
    площадка переставала показывать ссылки на источники.
    """
    before = dict(demo_workspace.settings or {})
    yield
    demo_workspace.settings = before
    await session.commit()


@pytest.fixture
async def client(session):
    app.dependency_overrides[get_session] = lambda: session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


async def send(session, text: str, *, external_id="tg-policy"):
    return await dialog.handle_incoming(
        session,
        channel="telegram",
        external_id=external_id,
        text=text,
        workspace_slug=WS,
    )


async def count_audit(session, workspace) -> int:
    return await session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.workspace_id == workspace.id)
    )


# ---------------------------------------------------------------------------
# значения по умолчанию
# ---------------------------------------------------------------------------


def test_defaults_are_all_on(workspace):
    """Ключа в JSON нет — защита работает.

    Иначе воркспейсы, заведённые до появления флагов, оказались бы с
    выключенной защитой, а новый флаг в будущем — выключенным у всех.
    """
    assert policy.security(workspace) == {
        "kb_only": True,
        "cite_sources": True,
        "audit_log": True,
        "mask_pii": True,
    }


def test_kb_only_cannot_be_turned_off(workspace):
    """«Отвечать только по базе знаний» — это продукт, а не настройка."""
    policy.apply(workspace, {"kb_only": False})
    assert policy.enabled(workspace, policy.KB_ONLY)


def test_apply_replaces_settings_dict(workspace):
    """SQLAlchemy не замечает мутацию вложенного JSONB.

    Правка на месте молча не сохранилась бы: настройка «применилась» на
    экране и вернулась после перезагрузки.
    """
    before = workspace.settings
    policy.apply(workspace, {"mask_pii": False})

    assert workspace.settings is not before
    assert workspace.settings["security"]["mask_pii"] is False


def test_apply_keeps_other_settings(workspace):
    """Приветствие живёт в тех же settings и потеряться не должно."""
    workspace.settings = {"greeting": "Салом!"}
    policy.apply(workspace, {"audit_log": False})
    assert workspace.settings["greeting"] == "Салом!"


# ---------------------------------------------------------------------------
# маскирование
# ---------------------------------------------------------------------------


async def test_masking_on_by_default(session, workspace):
    await send(session, f"Корти ман {CARD}")

    incoming = await session.scalar(
        select(Message)
        .where(Message.workspace_id == workspace.id, Message.role == "user")
        .order_by(Message.id.desc())
    )
    assert CARD in incoming.text
    assert "[CARD]" in incoming.text_masked


async def test_masking_can_be_turned_off(session, workspace):
    """Выключенный тумблер делает маску равной оригиналу.

    Колонки при этом местами не меняются: слева всегда оригинал, справа
    всегда то, что уйдёт в модель.
    """
    policy.apply(workspace, {"mask_pii": False})
    await session.flush()

    await send(session, f"Корти ман {CARD}")

    incoming = await session.scalar(
        select(Message)
        .where(Message.workspace_id == workspace.id, Message.role == "user")
        .order_by(Message.id.desc())
    )
    assert incoming.text == incoming.text_masked
    assert CARD in incoming.text_masked


# ---------------------------------------------------------------------------
# аудит-лог
# ---------------------------------------------------------------------------


async def test_escalation_is_written_to_audit(session, workspace):
    """Пункт 8 приёмки: в audit_log есть записи об обращениях."""
    await send(session, "Хочу оператора")

    item = await session.scalar(
        select(AuditLog)
        .where(AuditLog.workspace_id == workspace.id)
        .order_by(AuditLog.id.desc())
    )
    assert item is not None
    assert item.event == audit.EVENT_ESCALATION
    assert item.payload["reason"] == "user_request"


async def test_audit_can_be_turned_off(session, workspace):
    policy.apply(workspace, {"audit_log": False})
    await session.flush()
    before = await count_audit(session, workspace)

    await send(session, "Хочу оператора")

    assert await count_audit(session, workspace) == before


async def test_audit_failure_does_not_break_answer(session, workspace, monkeypatch):
    """Клиент ждёт ответа и не виноват в наших проблемах с записью следа."""

    def boom(*args, **kwargs):
        raise RuntimeError("база аудита недоступна")

    monkeypatch.setattr(session, "add", boom)

    # падение внутри audit.record не должно всплыть наружу
    await audit.record(session, workspace, audit.EVENT_LLM_CALL, {"x": 1})


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


async def test_workspace_endpoint_returns_model_and_flags(client, demo_workspace):
    """Экран 03 берёт отсюда имя модели: в эталоне оно выдумано."""
    from app.config import settings

    data = (await client.get("/api/workspace")).json()

    assert data["model"] == settings.SORO_MODEL
    assert set(data["security"]) == {
        "kb_only",
        "cite_sources",
        "audit_log",
        "mask_pii",
    }


async def test_put_security_saves_flag(client, session, demo_workspace):
    response = await client.put(
        "/api/workspace/security", json={"mask_pii": False}
    )

    assert response.status_code == 200
    assert response.json()["security"]["mask_pii"] is False
    await session.refresh(demo_workspace)
    assert not policy.enabled(demo_workspace, policy.MASK_PII)


async def test_put_security_ignores_kb_only(client, demo_workspace):
    """Схема запроса этого флага не содержит — лишнее поле молча отбросится,
    и «отвечать только по базе знаний» останется включённым."""
    response = await client.put(
        "/api/workspace/security", json={"kb_only": False}
    )
    assert response.json()["security"]["kb_only"] is True

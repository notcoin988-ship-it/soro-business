"""Тесты загрузки документов и воркера индексации (разделы 6.1, 6.3 ТЗ).

HTTP-слой проверяется без Redis: постановка в очередь подменяется — иначе
тест зависит от того, поднят ли воркер, и падает не по своей вине.

Воркер проверяется целиком, вместе с настоящими эмбеддингами: TEI поднят в
compose, а подменять его заглушкой значит не проверить главное — что
вектор нужной размерности доехал до базы.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.api import console
from app.db import get_session
from app.ingest.worker import _ingest
from app.main import app
from app.models import Chunk, Document

pytestmark = pytest.mark.usefixtures("demo_workspace")

PDF_TEXT = "Фоизи солона аз рӯи амонати «Ояндасоз» 14,5% дар як сол мебошад."


@pytest.fixture
def queue(monkeypatch):
    """Очередь-заглушка: запоминает, что поставили на индексацию."""
    jobs: list[int] = []
    monkeypatch.setattr(console, "enqueue", jobs.append)
    return jobs


@pytest.fixture
async def client(session):
    """HTTP-клиент, работающий в ТОЙ ЖЕ транзакции, что и тест.

    Без подмены зависимости эндпоинты открыли бы свою сессию и записали
    данные мимо отката — тесты оставляли бы мусор в базе разработки.
    """
    app.dependency_overrides[get_session] = lambda: session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def pdf(tmp_path):
    from app.tests import factories

    return factories.make_pdf(tmp_path / "tarify.pdf", [PDF_TEXT]).read_bytes()


# ---------------------------------------------------------------------------
# POST /api/documents
# ---------------------------------------------------------------------------


async def test_upload_returns_immediately_with_queued(client, queue, pdf):
    """Правило 6.1: индексация НИКОГДА не идёт внутри запроса.

    Ответ обязан прийти сразу со статусом `queued` — иначе загрузка
    40-страничного PDF повесит запрос на две минуты.
    """
    response = await client.post(
        "/api/documents", files={"file": ("Тарифы.pdf", pdf, "application/pdf")}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["kind"] == "pdf"
    assert body["title"] == "Тарифы"
    assert queue == [body["id"]], "документ не поставлен в очередь"


async def test_uploaded_file_saved_under_uuid(client, queue, pdf, session):
    """Имя на диске — uuid.

    В именах банковских файлов бывают пробелы, кириллица и нормализация
    NFD — на ней уже спотыкались, когда качали тарифы с сайта.
    """
    response = await client.post(
        "/api/documents",
        files={"file": ("Тарифы физлиц.pdf", pdf, "application/pdf")},
    )
    document = await session.get(Document, response.json()["id"])

    assert document.file_path.endswith(".pdf")
    assert "Тарифы" not in document.file_path


async def test_url_creates_web_document(client, queue):
    response = await client.post(
        "/api/documents", json={"url": "https://eskhata.tj/"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "web"
    assert body["source_url"] == "https://eskhata.tj/"
    assert queue == [body["id"]]


@pytest.mark.parametrize("name", ["notes.txt", "presentation.pptx", "scan.jpg"])
async def test_unsupported_extension_rejected(client, queue, name):
    response = await client.post(
        "/api/documents", files={"file": (name, b"content", "application/octet-stream")}
    )

    assert response.status_code == 415
    assert queue == []


async def test_url_must_be_http(client, queue):
    response = await client.post("/api/documents", json={"url": "ftp://bank.tj/doc"})
    assert response.status_code == 422
    assert queue == []


async def test_empty_request_is_explained(client, queue):
    response = await client.post("/api/documents", json={})
    assert response.status_code == 422
    assert queue == []


# ---------------------------------------------------------------------------
# GET и DELETE
# ---------------------------------------------------------------------------


async def test_list_shows_progress_for_screen_02(client, queue, pdf):
    created = (
        await client.post(
            "/api/documents", files={"file": ("Тарифы.pdf", pdf, "application/pdf")}
        )
    ).json()

    listed = (await client.get("/api/documents")).json()
    mine = next(d for d in listed if d["id"] == created["id"])

    # экран 02 рисует прогресс-бар из этих полей
    assert {"status", "chunks", "chunks_done", "chunks_total"} <= mine.keys()


async def test_delete_removes_document_and_file(client, queue, pdf, session):
    created = (
        await client.post(
            "/api/documents", files={"file": ("Тарифы.pdf", pdf, "application/pdf")}
        )
    ).json()
    document = await session.get(Document, created["id"])
    path = document.file_path

    response = await client.delete(f"/api/documents/{created['id']}")

    assert response.status_code == 204
    from pathlib import Path

    assert not Path(path).exists(), "файл остался на диске"


async def test_delete_unknown_document_is_404(client):
    assert (await client.delete("/api/documents/999999")).status_code == 404


# ---------------------------------------------------------------------------
# удаление сайта целиком (экран 02: страницы обхода свёрнуты в одну строку)
# ---------------------------------------------------------------------------


async def _add_page(session, workspace, url: str) -> Document:
    document = Document(
        workspace_id=workspace.id,
        kind="web",
        title=url,
        source_url=url,
        status="ready",
    )
    session.add(document)
    await session.commit()
    return document


async def test_delete_site_removes_all_its_pages(client, session, demo_workspace):
    """Обход даёт по строке на страницу — сносим их одним запросом.

    Иначе консоль шлёт полторы сотни DELETE подряд.
    """
    for path in ("/", "/tarify/", "/about/"):
        await _add_page(session, demo_workspace, f"https://bank.tj{path}")
    other = await _add_page(session, demo_workspace, "https://other.tj/")

    response = await client.delete("/api/documents", params={"host": "bank.tj"})

    assert response.status_code == 200
    assert response.json() == {"deleted": 3}
    # чужой сайт не задет
    assert await session.get(Document, other.id) is not None


async def test_delete_site_matches_host_exactly(client, session, demo_workspace):
    """Хост сравнивается разобранным, а не подстрокой.

    `LIKE '%bank.tj%'` снёс бы заодно `bank.tj.evil.com` — а это чужой
    сайт, который кто-то мог добавить намеренно.
    """
    victim = await _add_page(session, demo_workspace, "https://bank.tj.evil.com/x")
    await _add_page(session, demo_workspace, "https://bank.tj/")

    response = await client.delete("/api/documents", params={"host": "bank.tj"})

    assert response.json() == {"deleted": 1}
    assert await session.get(Document, victim.id) is not None


async def test_delete_site_unknown_host_is_404(client):
    response = await client.delete("/api/documents", params={"host": "нет-такого.tj"})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# воркер: настоящая индексация с настоящими эмбеддингами
# ---------------------------------------------------------------------------


async def test_worker_indexes_file_end_to_end(session, demo_workspace, tmp_path):
    from app.tests import factories

    path = factories.make_pdf(tmp_path / "tarify.pdf", [PDF_TEXT, PDF_TEXT])
    document = Document(
        workspace_id=demo_workspace.id,
        kind="pdf",
        title="Тарифҳо",
        file_path=str(path),
        status="queued",
    )
    session.add(document)
    await session.commit()

    result = await _ingest(document.id, session=session)

    await session.refresh(document)
    assert document.status == "ready"
    assert document.indexed_at is not None
    assert document.pages == 2
    assert result["chunks"] > 0

    chunks = (
        await session.scalars(select(Chunk).where(Chunk.document_id == document.id))
    ).all()
    assert len(chunks) == result["chunks"]

    # вектор нужной размерности и полнотекстовый индекс — без них поиск
    # раздела 6.4 работать не будет
    assert len(chunks[0].embedding) == 1024
    filled = await session.scalar(
        select(func.count())
        .select_from(Chunk)
        .where(Chunk.document_id == document.id, Chunk.tsv.isnot(None))
    )
    assert filled == len(chunks)

    # шапка из 6.3 на месте
    assert chunks[0].text.startswith("Документ: Тарифҳо. Страница 1.")


async def test_worker_marks_failure_with_reason(session, demo_workspace):
    """Причина падения обязана дойти до экрана 02.

    «Не удалось проиндексировать» без объяснения заставляет лезть в логи
    контейнера — на демо это не работает.
    """
    document = Document(
        workspace_id=demo_workspace.id,
        kind="pdf",
        title="Пропавший",
        file_path="/data/uploads/нет-такого.pdf",
        status="queued",
    )
    session.add(document)
    await session.commit()

    with pytest.raises(FileNotFoundError):
        await _ingest(document.id, session=session)

    await session.refresh(document)
    assert document.status == "failed"
    assert "FileNotFoundError" in document.error

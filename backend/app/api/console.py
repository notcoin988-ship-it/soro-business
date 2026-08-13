"""Эндпоинты консоли — база знаний (раздел 6.1 и приложение А ТЗ).

ГЛАВНОЕ ПРАВИЛО 6.1: индексация НИКОГДА не идёт внутри HTTP-запроса.
Файл сохраняется, документ создаётся со статусом `queued`, задача уходит в
RQ, ответ возвращается сразу. Иначе загрузка 40-страничного PDF повесит
запрос на две минуты, а браузер отвалится по таймауту раньше.

Состав:
  POST   /api/documents        файл (multipart) или {"url": "..."} для сайта
  GET    /api/documents        список для экрана 02 со статусом и прогрессом
  DELETE /api/documents/{id}   удаление вместе с фрагментами (ON DELETE CASCADE)
"""

from __future__ import annotations

import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel
from redis import Redis
from rq import Queue
from sqlalchemy import desc as sql_desc
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import audit, policy
from app.core.dialog import get_workspace
from app.db import get_session
from app.models import (
    ChannelIdentity,
    Chunk,
    Contact,
    Conversation,
    Document,
    Message,
)

# Сколько последних реплик отдаём экрану 04. Сорок — это примерно вдвое
# больше сценария из прототипа: длинный разговор на трёх устройствах уже
# не читается с проектора, а короткий помещается целиком.
OMNI_MESSAGE_LIMIT = 40

router = APIRouter(prefix="/api", tags=["console"])

# Разрешённые типы — те же, что в CHECK `documents.kind`.
SUFFIX_TO_KIND = {".pdf": "pdf", ".docx": "docx", ".xlsx": "xlsx"}
QUEUE_NAME = "ingest"
INGEST_TASK = "app.ingest.worker.ingest_document"
# Индексация большого PDF со сканами идёт минутами — очередь не должна
# считать задачу зависшей.
JOB_TIMEOUT = 3600


class DocumentIn(BaseModel):
    url: str


class DocumentOut(BaseModel):
    id: int
    kind: str
    title: str
    status: str
    source_url: str | None = None
    pages: int | None = None
    chunks: int = 0
    chunks_done: int = 0
    chunks_total: int = 0
    error: str | None = None


def enqueue(document_id: int) -> None:
    """Поставить индексацию в очередь.

    Вынесено в функцию, чтобы тесты подменяли её одной строкой и не
    поднимали Redis ради проверки HTTP-слоя.
    """
    queue = Queue(QUEUE_NAME, connection=Redis.from_url(settings.REDIS_URL))
    queue.enqueue(INGEST_TASK, document_id, job_timeout=JOB_TIMEOUT)


async def _create(
    session: AsyncSession,
    *,
    kind: str,
    title: str,
    source_url: str | None = None,
    file_path: str | None = None,
) -> Document:
    workspace = await get_workspace(session)
    document = Document(
        workspace_id=workspace.id,
        kind=kind,
        title=title,
        source_url=source_url,
        file_path=file_path,
        status="queued",
    )
    session.add(document)
    await session.flush()
    await audit.record(
        session,
        workspace,
        audit.EVENT_DOC_ADD,
        {
            "document_id": document.id,
            "kind": kind,
            "title": title,
            "source_url": source_url,
        },
    )
    await session.commit()
    enqueue(document.id)
    return document


# ---------------------------------------------------------------------------
# воркспейс и контур безопасности (экран 01)
# ---------------------------------------------------------------------------


class SecurityIn(BaseModel):
    """Изменения переключателей. Присылают только то, что дёрнули."""

    cite_sources: bool | None = None
    audit_log: bool | None = None
    mask_pii: bool | None = None


@router.get("/workspace")
async def get_workspace_info(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Всё, что консоли нужно знать о воркспейсе.

    `model` отдаём отсюда, а не хардкодим на фронте: в эталоне написано
    «Soro-27B · FP8», а на сервере крутится GPTQ-int4, и подпись на демо
    перед ИТ-службой банка обязана совпадать с тем, что реально отвечает.
    """
    workspace = await get_workspace(session)
    return {
        "slug": workspace.slug,
        "name": workspace.name,
        "model": settings.SORO_MODEL,
        "security": policy.security(workspace),
        # Экран 05 показывает сниппет для сайта банка и QR на бота. И то,
        # и другое — адреса ЭТОГО стенда: на демо через ngrok они меняются
        # при каждом перезапуске туннеля, и зашитый в код адрес означал бы
        # сниппет, который не работает, и QR, ведущий в никуда.
        "public_base_url": settings.PUBLIC_BASE_URL.rstrip("/"),
        "telegram_bot": settings.TELEGRAM_BOT_USERNAME,
    }


@router.put("/workspace/security")
async def update_security(
    payload: SecurityIn, session: AsyncSession = Depends(get_session)
) -> dict:
    """Переключатели контура безопасности.

    `kb_only` менять нельзя и в схему он не входит: «отвечать только по
    базе знаний» — это и есть продукт, а не настройка (см. `core/policy`).
    """
    workspace = await get_workspace(session)
    changes = payload.model_dump(exclude_none=True)
    flags = policy.apply(workspace, changes)
    await session.commit()
    return {"security": flags}


@router.post("/documents", response_model=DocumentOut, status_code=201)
async def add_document(
    request: Request,
    file: UploadFile | None = File(None),
    session: AsyncSession = Depends(get_session),
) -> DocumentOut:
    """Загрузка файла или ссылки на сайт. Ответ отдаётся сразу."""
    if file is not None and file.filename:
        suffix = Path(file.filename).suffix.lower()
        kind = SUFFIX_TO_KIND.get(suffix)
        if kind is None:
            raise HTTPException(
                status_code=415,
                detail=f"{suffix or 'файл без расширения'}: принимаем pdf, docx, xlsx",
            )

        workspace = await get_workspace(session)
        folder = Path(settings.UPLOAD_DIR) / workspace.slug
        folder.mkdir(parents=True, exist_ok=True)
        # имя на диске — uuid: в именах банковских файлов бывают пробелы,
        # кириллица и нормализация NFD, на которой ломаются ссылки
        path = folder / f"{uuid.uuid4()}{suffix}"
        path.write_bytes(await file.read())

        document = await _create(
            session,
            kind=kind,
            title=Path(file.filename).stem,
            file_path=str(path),
        )
        return await _to_out(session, document)

    # не multipart — значит ссылка на сайт
    try:
        payload = DocumentIn.model_validate(await request.json())
    except Exception:
        raise HTTPException(
            status_code=422,
            detail='нужен файл (multipart) или JSON вида {"url": "https://..."}',
        ) from None

    if not payload.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="url должен начинаться с http(s)")

    document = await _create(
        session,
        kind="web",
        title=payload.url,
        source_url=payload.url,
    )
    return await _to_out(session, document)


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    session: AsyncSession = Depends(get_session),
) -> list[DocumentOut]:
    """Список для экрана 02. Консоль опрашивает его, пока есть незавершённые."""
    workspace = await get_workspace(session)
    documents = (
        await session.scalars(
            select(Document)
            .where(Document.workspace_id == workspace.id)
            .order_by(Document.id.desc())
        )
    ).all()
    return [await _to_out(session, d) for d in documents]


@router.delete("/documents")
async def delete_site(
    host: str, session: AsyncSession = Depends(get_session)
) -> dict:
    """Удалить все страницы одного сайта разом.

    Обход даёт по строке `documents` на страницу — у Эсхаты их полторы
    сотни. Удалять их по одной из браузера значит полторы сотни запросов
    и полминуты ожидания, поэтому у консоли есть этот эндпоинт: на экране
    02 страницы сайта свёрнуты в одну строку, и корзина на ней сносит
    сайт целиком.

    Хост сравниваем разобранным, а не по `LIKE '%eskhata.com%'`: подстрока
    зацепила бы и `not-eskhata.com.evil.tj`.
    """
    workspace = await get_workspace(session)
    wanted = urlsplit(host if "//" in host else f"//{host}").netloc.lower()
    if not wanted:
        raise HTTPException(status_code=422, detail="нужен хост, например eskhata.com")

    documents = (
        await session.scalars(
            select(Document)
            .where(Document.workspace_id == workspace.id)
            .where(Document.kind == "web")
            .where(Document.source_url.is_not(None))
        )
    ).all()

    deleted = 0
    for document in documents:
        if urlsplit(document.source_url).netloc.lower() != wanted:
            continue
        await session.delete(document)
        deleted += 1

    if not deleted:
        raise HTTPException(status_code=404, detail=f"страниц сайта {wanted} нет")

    await session.commit()
    return {"deleted": deleted}


# response_model=None нужен явно: из аннотации `-> None` FastAPI выводит
# модель ответа NoneType, а она считается телом — и 204 не проходит проверку
# «у этого кода тела быть не должно»
@router.delete(
    "/documents/{document_id}",
    status_code=204,
    response_class=Response,
    response_model=None,
)
async def delete_document(
    document_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="документ не найден")

    # файл с диска убираем сами: ON DELETE CASCADE знает только про строки
    if document.file_path:
        Path(document.file_path).unlink(missing_ok=True)

    await session.delete(document)
    await session.commit()


@router.get("/omni/latest")
async def omni_latest(session: AsyncSession = Depends(get_session)) -> dict:
    """Настоящий омниканальный диалог для экрана 04.

    ЭТОГО ЭНДПОИНТА НЕТ В ПРИЛОЖЕНИИ А. Экран 04 по ТЗ презентационный:
    пятнадцать шагов сценария и никакого бэкенда. Сценарий и остаётся —
    но рядом с ним экран показывает диалог, который действительно
    случился, и это единственное место в демо, где омниканальность можно
    не пообещать, а предъявить: те же три устройства, только сообщения
    настоящие.

    Берём диалог, в котором больше всего РАЗНЫХ каналов, из свежих —
    последний. Один канал тоже отдаём: показать «пока только Telegram»
    честнее, чем пустой экран.
    """
    workspace = await get_workspace(session)

    best = (
        await session.execute(
            select(
                Message.conversation_id,
                func.count(func.distinct(Message.channel)).label("channels"),
                func.max(Message.id).label("last"),
            )
            .where(Message.workspace_id == workspace.id, Message.role != "system")
            .group_by(Message.conversation_id)
            .order_by(sql_desc("channels"), sql_desc("last"))
            .limit(1)
        )
    ).first()

    if best is None:
        return {"empty": True, "messages": [], "channels": [], "identities": []}

    conversation = await session.get(Conversation, best.conversation_id)
    contact = await session.get(Contact, conversation.contact_id)
    identities = (
        await session.scalars(
            select(ChannelIdentity).where(
                ChannelIdentity.contact_id == conversation.contact_id
            )
        )
    ).all()

    messages = (
        await session.scalars(
            select(Message)
            .where(
                Message.conversation_id == conversation.id,
                Message.role != "system",
            )
            .order_by(Message.id.desc())
            .limit(OMNI_MESSAGE_LIMIT)
        )
    ).all()

    return {
        "empty": False,
        "conversation_id": conversation.id,
        "status": conversation.status,
        "contact": {
            "id": contact.id if contact else None,
            "display_name": contact.display_name if contact else None,
        },
        # Склейка — главное, что показывает этот блок: два внешних id,
        # один человек.
        "identities": [
            {"channel": identity.channel, "external_id": identity.external_id}
            for identity in identities
        ],
        "channels": sorted({message.channel for message in messages}),
        "messages": [
            {
                "channel": message.channel,
                "role": message.role,
                # Маска, а не оригинал: экран 04 показывают на встрече с
                # проектора, и номер карты клиента там светить нельзя.
                # Инбокс оператора — другое дело, там нужен оригинал.
                "text": message.text_masked or message.text,
                "created_at": message.created_at.isoformat(),
            }
            for message in reversed(messages)
        ],
    }


async def _to_out(session: AsyncSession, document: Document) -> DocumentOut:
    chunks = await session.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.document_id == document.id)
    )
    progress = document.settings or {}
    return DocumentOut(
        id=document.id,
        kind=document.kind,
        title=document.title,
        status=document.status,
        source_url=document.source_url,
        pages=document.pages,
        chunks=chunks or 0,
        chunks_done=progress.get("chunks_done", 0),
        chunks_total=progress.get("chunks_total", 0),
        error=document.error,
    )

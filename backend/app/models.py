"""Все SQLAlchemy-модели. Структура совпадает с эталонным DDL раздела 5 ТЗ.

Во ВСЕХ таблицах есть workspace_id — задел под много банков (раздел 1.2).
Любое изменение схемы — только через миграцию Alembic.
"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
# импортируем под псевдонимом: в моделях Message и Chunk есть колонка `text`,
# которая внутри тела класса перекрыла бы одноимённую функцию SQLAlchemy
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.db import Base

TZ = DateTime(timezone=True)


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)  # 'eskhata-demo'
    name: Mapped[str] = mapped_column(Text, nullable=False)  # 'Банк Эсхата'
    # тон, приветствие, языки
    settings: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        TZ, nullable=False, server_default=func.now()
    )


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id"), nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(Text)
    first_seen: Mapped[datetime] = mapped_column(
        TZ, nullable=False, server_default=func.now()
    )
    # см. 5.1 про склейку контактов
    merged_into: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("contacts.id")
    )


class ChannelIdentity(Base):
    __tablename__ = "channel_identities"
    __table_args__ = (
        CheckConstraint(
            "channel IN ('telegram','widget','whatsapp')",
            name="ck_channel_identities_channel",
        ),
        UniqueConstraint(
            "workspace_id", "channel", "external_id", name="uq_channel_identity"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id"), nullable=False
    )
    contact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("contacts.id"), nullable=False
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    # tg user id / widget uuid / wa phone
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('bot','operator','closed')", name="ck_conversations_status"
        ),
        Index("ix_conv_ws_status", "workspace_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id"), nullable=False
    )
    contact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("contacts.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'bot'")
    )
    started_at: Mapped[datetime] = mapped_column(
        TZ, nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(TZ)
    last_msg_at: Mapped[datetime] = mapped_column(
        TZ, nullable=False, server_default=func.now()
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user','assistant','operator','system')",
            name="ck_messages_role",
        ),
        Index("ix_msg_conv", "conversation_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id"), nullable=False
    )
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id"), nullable=False
    )
    # канал, из которого пришло/ушло
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    # оригинал: оператор должен видеть настоящий номер
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # то, что реально ушло в модель
    text_masked: Mapped[str | None] = mapped_column(Text)
    chunks_used: Mapped[list[int]] = mapped_column(
        ARRAY(BigInteger), server_default=sql_text("'{}'")
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        TZ, nullable=False, server_default=func.now()
    )


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('pdf','docx','xlsx','web')", name="ck_documents_kind"
        ),
        CheckConstraint(
            "status IN ('queued','indexing','ready','failed')",
            name="ck_documents_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)  # для kind='web'
    file_path: Mapped[str | None] = mapped_column(Text)  # путь в volume для файлов
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sql_text("'queued'")
    )
    error: Mapped[str | None] = mapped_column(Text)
    pages: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        TZ, nullable=False, server_default=func.now()
    )
    indexed_at: Mapped[datetime | None] = mapped_column(TZ)
    # ОТКЛОНЕНИЕ ОТ DDL: раздел 9 требует
    # прогресс-бар индексации «из поля chunks_done/chunks_total
    # в documents.settings», но в DDL раздела 5 такой колонки нет.
    settings: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index(
            "ix_chunks_vec",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id"), nullable=False
    )
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page: Mapped[int | None] = mapped_column(Integer)  # страница/лист/URL-хвост
    ord: Mapped[int] = mapped_column(Integer, nullable=False)  # порядковый номер
    text: Mapped[str] = mapped_column(Text, nullable=False)
    tsv: Mapped[str | None] = mapped_column(TSVECTOR)  # полнотекстовый вектор, см. 6.4
    embedding: Mapped[list[float]] = mapped_column(
        Vector(settings.EMBEDDINGS_DIM), nullable=False
    )


class Escalation(Base):
    __tablename__ = "escalations"
    __table_args__ = (
        CheckConstraint(
            "reason IN ('no_answer','low_confidence','user_request','pii_topic')",
            name="ck_escalations_reason",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("workspaces.id"), nullable=False
    )
    conversation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("conversations.id"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZ, nullable=False, server_default=func.now()
    )
    taken_by: Mapped[str | None] = mapped_column(Text)  # логин оператора
    taken_at: Mapped[datetime | None] = mapped_column(TZ)
    resolved_at: Mapped[datetime | None] = mapped_column(TZ)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 'llm_call','escalation','doc_add'...
    event: Mapped[str] = mapped_column(Text, nullable=False)
    # запрос, chunk_ids, латентность
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZ, nullable=False, server_default=func.now()
    )


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (
        CheckConstraint("score IN (-1, 1)", name="ck_feedback_score"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("messages.id"), nullable=False
    )
    score: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TZ, nullable=False, server_default=func.now()
    )


__all__ = [
    "Workspace",
    "Contact",
    "ChannelIdentity",
    "Conversation",
    "Message",
    "Document",
    "Chunk",
    "Escalation",
    "AuditLog",
    "Feedback",
]

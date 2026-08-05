"""Начальная схема — эталонный DDL раздела 5 ТЗ.

SQL приведён теми же операторами, что в документе, чтобы миграцию можно было
построчно сверить с ТЗ. Единственное отклонение — колонка documents.settings,
решение принято, колонка оставлена.

Revision ID: 0001
Revises:
Create Date: 2026-08-03
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    op.execute(
        """
        CREATE TABLE workspaces (
          id           BIGSERIAL PRIMARY KEY,
          slug         TEXT UNIQUE NOT NULL,          -- 'eskhata-demo'
          name         TEXT NOT NULL,                 -- 'Банк Эсхата'
          settings     JSONB NOT NULL DEFAULT '{}',   -- тон, приветствие, языки
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )

    op.execute(
        """
        CREATE TABLE contacts (
          id           BIGSERIAL PRIMARY KEY,
          workspace_id BIGINT NOT NULL REFERENCES workspaces(id),
          display_name TEXT,
          first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
          merged_into  BIGINT REFERENCES contacts(id)  -- см. 5.1 про склейку
        );
        """
    )

    op.execute(
        """
        CREATE TABLE channel_identities (
          id           BIGSERIAL PRIMARY KEY,
          workspace_id BIGINT NOT NULL REFERENCES workspaces(id),
          contact_id   BIGINT NOT NULL REFERENCES contacts(id),
          channel      TEXT NOT NULL CHECK (channel IN
                       ('telegram','widget','whatsapp')),
          external_id  TEXT NOT NULL,   -- tg user id / widget uuid / wa phone
          meta         JSONB NOT NULL DEFAULT '{}',
          UNIQUE (workspace_id, channel, external_id)
        );
        """
    )

    op.execute(
        """
        CREATE TABLE conversations (
          id           BIGSERIAL PRIMARY KEY,
          workspace_id BIGINT NOT NULL REFERENCES workspaces(id),
          contact_id   BIGINT NOT NULL REFERENCES contacts(id),
          status       TEXT NOT NULL DEFAULT 'bot' CHECK (status IN
                       ('bot','operator','closed')),
          started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          closed_at    TIMESTAMPTZ,
          last_msg_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute(
        "CREATE INDEX ix_conv_ws_status ON conversations(workspace_id,status);"
    )

    op.execute(
        """
        CREATE TABLE messages (
          id              BIGSERIAL PRIMARY KEY,
          workspace_id    BIGINT NOT NULL REFERENCES workspaces(id),
          conversation_id BIGINT NOT NULL REFERENCES conversations(id),
          channel         TEXT NOT NULL,   -- канал, из которого пришло/ушло
          role            TEXT NOT NULL CHECK (role IN
                          ('user','assistant','operator','system')),
          text            TEXT NOT NULL,
          text_masked     TEXT,            -- то, что реально ушло в модель
          chunks_used     BIGINT[] DEFAULT '{}',
          latency_ms      INT,
          tokens_in       INT,
          tokens_out      INT,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX ix_msg_conv ON messages(conversation_id, created_at);")

    op.execute(
        """
        CREATE TABLE documents (
          id           BIGSERIAL PRIMARY KEY,
          workspace_id BIGINT NOT NULL REFERENCES workspaces(id),
          kind         TEXT NOT NULL CHECK (kind IN
                       ('pdf','docx','xlsx','web')),
          title        TEXT NOT NULL,
          source_url   TEXT,              -- для kind='web'
          file_path    TEXT,              -- путь в volume для файлов
          status       TEXT NOT NULL DEFAULT 'queued' CHECK (status IN
                       ('queued','indexing','ready','failed')),
          error        TEXT,
          pages        INT,
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          indexed_at   TIMESTAMPTZ,
          -- отклонение от DDL: прогресс индексации для экрана 02,
          -- раздел 9 ссылается на documents.settings
          settings     JSONB NOT NULL DEFAULT '{}'
        );
        """
    )

    op.execute(
        """
        CREATE TABLE chunks (
          id           BIGSERIAL PRIMARY KEY,
          workspace_id BIGINT NOT NULL REFERENCES workspaces(id),
          document_id  BIGINT NOT NULL REFERENCES documents(id)
                       ON DELETE CASCADE,
          page         INT,               -- страница/лист/URL-хвост
          ord          INT NOT NULL,      -- порядковый номер в документе
          text         TEXT NOT NULL,
          tsv          TSVECTOR,          -- полнотекстовый вектор, см. 6.4
          embedding    VECTOR(1024) NOT NULL
        );
        """
    )
    op.execute(
        "CREATE INDEX ix_chunks_vec ON chunks "
        "USING hnsw (embedding vector_cosine_ops);"
    )
    op.execute("CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv);")

    op.execute(
        """
        CREATE TABLE escalations (
          id              BIGSERIAL PRIMARY KEY,
          workspace_id    BIGINT NOT NULL REFERENCES workspaces(id),
          conversation_id BIGINT NOT NULL REFERENCES conversations(id),
          reason          TEXT NOT NULL CHECK (reason IN
                          ('no_answer','low_confidence','user_request',
                           'pii_topic')),
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          taken_by        TEXT,             -- логин оператора
          taken_at        TIMESTAMPTZ,
          resolved_at     TIMESTAMPTZ
        );
        """
    )

    op.execute(
        """
        CREATE TABLE audit_log (
          id           BIGSERIAL PRIMARY KEY,
          workspace_id BIGINT NOT NULL,
          event        TEXT NOT NULL,   -- 'llm_call','escalation','doc_add'...
          payload      JSONB NOT NULL,  -- запрос, chunk_ids, латентность
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )

    op.execute(
        """
        CREATE TABLE feedback (
          id           BIGSERIAL PRIMARY KEY,
          workspace_id BIGINT NOT NULL,
          message_id   BIGINT NOT NULL REFERENCES messages(id),
          score        SMALLINT NOT NULL CHECK (score IN (-1, 1)),
          created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )


def downgrade() -> None:
    for table in (
        "feedback",
        "audit_log",
        "escalations",
        "chunks",
        "documents",
        "messages",
        "conversations",
        "channel_identities",
        "contacts",
        "workspaces",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")

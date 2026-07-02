-- Reference DDL (PostgreSQL dialect) for the AI Video API.
-- The app auto-creates tables from ORM metadata for SQLite; this file documents
-- the canonical production schema and is the base for Alembic migrations.

CREATE TABLE IF NOT EXISTS partners (
    id               BIGSERIAL PRIMARY KEY,
    name             VARCHAR(200) NOT NULL,
    email            VARCHAR(320),
    balance_credits  BIGINT NOT NULL DEFAULT 0,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS api_keys (
    id           BIGSERIAL PRIMARY KEY,
    partner_id   BIGINT NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
    prefix       VARCHAR(16) NOT NULL,
    key_hash     VARCHAR(64) NOT NULL UNIQUE,
    label        VARCHAR(120),
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    last_used_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_api_keys_prefix ON api_keys(prefix);
CREATE INDEX IF NOT EXISTS ix_api_keys_partner ON api_keys(partner_id);

CREATE TABLE IF NOT EXISTS uploads (
    id           VARCHAR(36) PRIMARY KEY,
    partner_id   BIGINT REFERENCES partners(id) ON DELETE SET NULL,
    filename     VARCHAR(255) NOT NULL,
    stored_path  VARCHAR(1024) NOT NULL,
    comfy_name   VARCHAR(255) NOT NULL,
    url          VARCHAR(2048) NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    size_bytes   BIGINT NOT NULL,
    width        INTEGER NOT NULL,
    height       INTEGER NOT NULL,
    sha256       VARCHAR(64) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_uploads_sha ON uploads(sha256);

CREATE TABLE IF NOT EXISTS tasks (
    id                VARCHAR(36) PRIMARY KEY,
    partner_id        BIGINT REFERENCES partners(id) ON DELETE SET NULL,
    user_id           VARCHAR(128),
    request_id        VARCHAR(128),
    task_type         VARCHAR(16) NOT NULL DEFAULT 'video',
    mode              VARCHAR(120) NOT NULL,
    status            VARCHAR(20) NOT NULL DEFAULT 'queued',
    progress          INTEGER NOT NULL DEFAULT 0,
    prompt            TEXT,
    negative_prompt   TEXT,
    image_url         VARCHAR(2048),
    callback_url      VARCHAR(2048),
    resolved_params   JSONB NOT NULL DEFAULT '{}'::jsonb,
    request_metadata  JSONB,
    price_credits     INTEGER NOT NULL DEFAULT 0,
    comfy_prompt_id   VARCHAR(64),
    comfy_endpoint    VARCHAR(128),
    result_url        VARCHAR(2048),
    result_path       VARCHAR(1024),
    duration_ms       INTEGER,
    error_code        VARCHAR(64),
    error_message     TEXT,
    attempts          INTEGER NOT NULL DEFAULT 0,
    started_at        TIMESTAMPTZ,
    finished_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_tasks_idempotency UNIQUE (partner_id, request_id)
);
CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS ix_tasks_partner_created ON tasks(partner_id, created_at);
CREATE INDEX IF NOT EXISTS ix_tasks_comfy_prompt ON tasks(comfy_prompt_id);

CREATE TABLE IF NOT EXISTS billing_entries (
    id          BIGSERIAL PRIMARY KEY,
    partner_id  BIGINT NOT NULL REFERENCES partners(id) ON DELETE CASCADE,
    task_id     VARCHAR(36) REFERENCES tasks(id) ON DELETE SET NULL,
    entry_type  VARCHAR(16) NOT NULL DEFAULT 'charge',
    amount      BIGINT NOT NULL,
    note        VARCHAR(500),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_billing_partner_created ON billing_entries(partner_id, created_at);

CREATE TABLE IF NOT EXISTS event_logs (
    id          BIGSERIAL PRIMARY KEY,
    task_id     VARCHAR(36),
    partner_id  BIGINT,
    level       VARCHAR(16) NOT NULL DEFAULT 'INFO',
    source      VARCHAR(32) NOT NULL DEFAULT 'api',
    event       VARCHAR(120) NOT NULL,
    message     TEXT,
    data        JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_event_logs_task ON event_logs(task_id);

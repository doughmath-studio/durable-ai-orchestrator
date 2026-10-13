"""Миграции схемы SQLite.

Версия схемы хранится в PRAGMA user_version. Каждая миграция применяется
атомарно (одна транзакция). Новые миграции только ДОБАВЛЯЮТСЯ в конец списка,
старые никогда не редактируются.
"""

MIGRATION_1 = """
CREATE TABLE tasks (
    id                INTEGER PRIMARY KEY,
    title             TEXT    NOT NULL CHECK (length(title) BETWEEN 1 AND 500),
    description       TEXT    NOT NULL DEFAULT '',
    status            TEXT    NOT NULL DEFAULT 'PENDING' CHECK (status IN
                      ('PENDING','READY','RUNNING','VERIFYING','RETRY_WAIT',
                       'BLOCKED','FAILED','COMPLETED','CANCELLED')),
    priority          INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL,
    available_at      TEXT    NOT NULL,
    attempt_count     INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    max_attempts      INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 20),
    idempotency_key   TEXT    UNIQUE,
    request_hash      TEXT,
    parent_task_id    INTEGER REFERENCES tasks(id) ON DELETE RESTRICT,
    required_capability TEXT,
    assigned_provider TEXT,
    side_effects      TEXT    NOT NULL DEFAULT 'none'
                      CHECK (side_effects IN ('none','idempotent','external')),
    result_summary    TEXT,
    error_summary     TEXT,
    blocked_reason    TEXT,
    resume_condition  TEXT,
    cancel_requested  INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0,1)),
    restart_count     INTEGER NOT NULL DEFAULT 0 CHECK (restart_count >= 0),
    deadline_at       TEXT,
    CHECK (attempt_count <= max_attempts),
    CHECK (status <> 'BLOCKED' OR blocked_reason IS NOT NULL)
);
CREATE INDEX idx_tasks_queue  ON tasks(status, available_at, priority);
CREATE INDEX idx_tasks_parent ON tasks(parent_task_id);

CREATE TABLE task_dependencies (
    task_id            INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on_task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    PRIMARY KEY (task_id, depends_on_task_id),
    CHECK (task_id <> depends_on_task_id)
);
CREATE INDEX idx_deps_reverse ON task_dependencies(depends_on_task_id);

CREATE TABLE task_attempts (
    id                  INTEGER PRIMARY KEY,
    task_id             INTEGER NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    attempt_number      INTEGER NOT NULL CHECK (attempt_number >= 1),
    attempt_uid         TEXT    NOT NULL UNIQUE,
    provider            TEXT,
    status              TEXT    NOT NULL CHECK (status IN
                        ('running','succeeded','failed','interrupted')),
    started_at          TEXT    NOT NULL,
    finished_at         TEXT,
    lease_expires_at    TEXT    NOT NULL,
    input_snapshot      TEXT,
    output_snapshot     TEXT,
    error               TEXT,
    error_kind          TEXT CHECK (error_kind IS NULL OR error_kind IN
                        ('transient','permanent','needs_human','interrupted')),
    verification_result TEXT,
    UNIQUE (task_id, attempt_number),
    CHECK ((status = 'running') = (finished_at IS NULL))
);
-- Не больше одной выполняющейся попытки на задачу.
CREATE UNIQUE INDEX idx_one_running_attempt ON task_attempts(task_id) WHERE status = 'running';

CREATE TABLE events (
    id             INTEGER PRIMARY KEY,
    event_id       TEXT    NOT NULL UNIQUE,
    task_id        INTEGER REFERENCES tasks(id) ON DELETE RESTRICT,
    event_type     TEXT    NOT NULL,
    timestamp      TEXT    NOT NULL,
    payload        TEXT    NOT NULL DEFAULT '{}',
    correlation_id TEXT
);
CREATE INDEX idx_events_task ON events(task_id, id);
-- Журнал только дописывается: изменить или удалить событие нельзя.
CREATE TRIGGER events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;

CREATE TABLE checkpoints (
    id                 INTEGER PRIMARY KEY,
    task_id            INTEGER NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    created_at         TEXT    NOT NULL,
    checkpoint_version INTEGER NOT NULL CHECK (checkpoint_version >= 1),
    state_json         TEXT    NOT NULL,
    artifact_refs      TEXT    NOT NULL DEFAULT '[]',
    checksum           TEXT    NOT NULL,
    UNIQUE (task_id, checkpoint_version)
);

CREATE TABLE artifacts (
    id            INTEGER PRIMARY KEY,
    task_id       INTEGER NOT NULL REFERENCES tasks(id) ON DELETE RESTRICT,
    artifact_type TEXT    NOT NULL,
    path_or_uri   TEXT    NOT NULL,
    checksum      TEXT    NOT NULL,
    created_at    TEXT    NOT NULL,
    metadata_json TEXT    NOT NULL DEFAULT '{}',
    UNIQUE (task_id, path_or_uri, checksum)
);

CREATE TABLE providers (
    id                INTEGER PRIMARY KEY,
    provider_name     TEXT    NOT NULL UNIQUE,
    enabled           INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    capabilities_json TEXT    NOT NULL DEFAULT '[]',
    last_health_check TEXT,
    unavailable_until TEXT,
    quota_state_json  TEXT    NOT NULL DEFAULT '{}'
);
"""

MIGRATIONS = [MIGRATION_1]

import uuid
import json
import hashlib
from datetime import datetime, timezone
from typing import Optional
from db import Database
from models import Task, TaskStatus, VALID_TRANSITIONS
from error_classifier import ErrorClassifier, ErrorCategory, RetryManager

class TaskManager:
    def __init__(self, db: Database):
        self.db = db
        self.retry_manager = RetryManager(base_delay=5, max_delay=300)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _log_event(self, conn, task_id: str, event_type: str, payload: dict):
        conn.execute(
            "INSERT INTO events (event_id, task_id, event_type, payload) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), task_id, event_type, json.dumps(payload, ensure_ascii=False))
        )

    def create_task(self, title: str, description: str = "", max_attempts: int = 3, idempotency_key: Optional[str] = None) -> Task:
        now = self._now()
        task_id = str(uuid.uuid4())
        
        with self.db.get_connection() as conn:
            if idempotency_key:
                cur = conn.execute("SELECT id FROM tasks WHERE idempotency_key = ?", (idempotency_key,))
                existing = cur.fetchone()
                if existing:
                    return self.get_task(existing['id'])

            conn.execute(
                "INSERT INTO tasks (id, title, description, status, created_at, updated_at, available_at, max_attempts, idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, title, description, TaskStatus.PENDING.value, now, now, now, max_attempts, idempotency_key)
            )
            self._log_event(conn, task_id, "TASK_CREATED", {"title": title})
            conn.commit()

        self.refresh_task_status(task_id)
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> Optional[Task]:
        with self.db.get_connection() as conn:
            cur = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = cur.fetchone()
            return Task(**dict(row)) if row else None

    def update_status(self, task_id: str, new_status: TaskStatus, blocked_reason: str = None, result_summary: str = None, error_summary: str = None):
        now = self._now()
        with self.db.get_connection() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ?, blocked_reason = ?, result_summary = ?, error_summary = ? WHERE id = ?",
                (new_status.value, now, blocked_reason, result_summary, error_summary, task_id)
            )
            self._log_event(conn, task_id, "STATUS_CHANGED", {"to": new_status.value})
            conn.commit()

    def refresh_task_status(self, task_id: str):
        task = self.get_task(task_id)
        if task and task.status in (TaskStatus.PENDING, TaskStatus.BLOCKED, TaskStatus.RETRY_WAIT):
            self.update_status(task_id, TaskStatus.READY)

    def record_attempt_failure(self, task_id: str, error: Exception):
        task = self.get_task(task_id)
        if not task: return
        category = ErrorClassifier.classify(error)
        new_attempts = task.attempt_count + 1
        now = self._now()

        with self.db.get_connection() as conn:
            if category == ErrorCategory.FATAL or new_attempts >= task.max_attempts:
                conn.execute("UPDATE tasks SET status = 'FAILED', error_summary = ?, updated_at = ? WHERE id = ?", (str(error), now, task_id))
            else:
                next_available = self.retry_manager.calculate_next_available_time(new_attempts)
                conn.execute("UPDATE tasks SET attempt_count = ?, status = 'RETRY_WAIT', available_at = ?, error_summary = ?, updated_at = ? WHERE id = ?", (new_attempts, next_available, str(error), now, task_id))
            conn.commit()

    def get_next_ready_task(self) -> Optional[Task]:
        now = self._now()
        with self.db.get_connection() as conn:
            cur = conn.execute("SELECT * FROM tasks WHERE (status = 'READY' OR (status = 'RETRY_WAIT' AND available_at <= ?)) ORDER BY priority DESC, created_at ASC LIMIT 1", (now,))
            row = cur.fetchone()
            return Task(**dict(row)) if row else None

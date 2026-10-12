from enum import Enum
from dataclasses import dataclass
from typing import Optional

class TaskStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    RETRY_WAIT = "RETRY_WAIT"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"

VALID_TRANSITIONS = {
    TaskStatus.PENDING: {TaskStatus.READY, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
    TaskStatus.READY: {TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {TaskStatus.VERIFYING, TaskStatus.RETRY_WAIT, TaskStatus.BLOCKED, TaskStatus.FAILED},
    TaskStatus.VERIFYING: {TaskStatus.COMPLETED, TaskStatus.RETRY_WAIT, TaskStatus.BLOCKED, TaskStatus.FAILED},
    TaskStatus.RETRY_WAIT: {TaskStatus.READY, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
    TaskStatus.BLOCKED: {TaskStatus.PENDING, TaskStatus.READY, TaskStatus.CANCELLED},
    TaskStatus.FAILED: {TaskStatus.PENDING, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.CANCELLED: set()
}

@dataclass
class Task:
    id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    priority: int = 0
    created_at: str = ""
    updated_at: str = ""
    available_at: str = ""
    attempt_count: int = 0
    max_attempts: int = 3
    idempotency_key: Optional[str] = None
    parent_task_id: Optional[str] = None
    assigned_provider: Optional[str] = None
    result_summary: Optional[str] = None
    error_summary: Optional[str] = None
    blocked_reason: Optional[str] = None

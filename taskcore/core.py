"""TaskCore — устойчивое ядро задач.

Вся правда о состоянии хранится в SQLite. Любое изменение состояния делается
в одной транзакции вместе с записью события в журнал: либо записано всё,
либо ничего.
"""
import hashlib
import json
import random
import re
import time
import uuid
from datetime import datetime, timezone

from .db import Database
from .errors import (
    AttemptAlreadyFinishedError, CheckpointCorruptError, DependencyCycleError,
    ExecutorError, IdempotencyConflictError, InvalidTransitionError,
    NotFoundError, ProviderUnavailableError, SecretDetectedError,
    StaleAttemptError, ValidationError,
)

# ---------------------------------------------------------------------------
# Конечный автомат
# ---------------------------------------------------------------------------

STATUSES = ("PENDING", "READY", "RUNNING", "VERIFYING", "RETRY_WAIT",
            "BLOCKED", "FAILED", "COMPLETED", "CANCELLED")
TERMINAL = {"COMPLETED", "CANCELLED"}

TRANSITIONS = {
    "PENDING":    {"READY", "BLOCKED", "CANCELLED"},
    "READY":      {"RUNNING", "BLOCKED", "CANCELLED"},
    # CANCELLED из RUNNING/VERIFYING добавлен к исходной спецификации:
    # без него задачу, которая уже выполняется, нельзя было бы отменить.
    # Разрешён только через cancel() (кооперативная отмена).
    "RUNNING":    {"VERIFYING", "RETRY_WAIT", "BLOCKED", "FAILED", "CANCELLED"},
    "VERIFYING":  {"COMPLETED", "RETRY_WAIT", "BLOCKED", "FAILED", "CANCELLED"},
    "RETRY_WAIT": {"READY", "BLOCKED", "CANCELLED"},
    "BLOCKED":    {"PENDING", "READY", "CANCELLED"},
    "FAILED":     {"PENDING", "CANCELLED"},
    "COMPLETED":  set(),
    "CANCELLED":  set(),
}

# Переходы, которые нельзя сделать "вручную" через transition():
# они принадлежат конкретным операциям ядра, чтобы попытки, журнал
# и счётчики всегда оставались согласованными.
_INTERNAL_ONLY_TARGETS = {"RUNNING", "VERIFYING", "COMPLETED", "RETRY_WAIT"}
_INTERNAL_ONLY_SOURCES = {"RUNNING", "VERIFYING", "FAILED"}


def is_allowed(src, dst):
    return dst in TRANSITIONS.get(src, set())


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_SECRET_KEY = re.compile(r"(api[_-]?key|secret|token|password|passwd|authorization|bearer)", re.I)
_SECRET_VALUE = re.compile(r"(sk-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,})")


def find_secrets(obj, path=""):
    """Возвращает пути к полям, похожим на секреты."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            if _SECRET_KEY.search(str(k)):
                found.append(p)
            found.extend(find_secrets(v, p))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            found.extend(find_secrets(v, f"{path}[{i}]"))
    elif isinstance(obj, str) and _SECRET_VALUE.search(obj):
        found.append(path or "<value>")
    return found


def _row(r):
    return dict(r) if r is not None else None


# ---------------------------------------------------------------------------
# Ядро
# ---------------------------------------------------------------------------

class TaskCore:
    """Главный объект. Пример: core = TaskCore("data/taskcore.db")."""

    def __init__(self, db_path=":memory:", *, clock=time.time, rng=random.random,
                 backoff_base=5.0, backoff_cap=3600.0, lease_seconds=600.0,
                 timeout=5.0):
        self.db = Database(db_path, timeout=timeout)
        self.clock = clock
        self.rng = rng
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.lease_seconds = lease_seconds

    @property
    def conn(self):
        return self.db.conn

    def close(self):
        self.db.close()

    def now(self):
        return iso(self.clock())

    # ----------------------------------------------------------- журнал ---

    def log_event(self, event_type, task_id=None, payload=None, *,
                  event_id=None, correlation_id=None):
        """Записывает событие. Повтор с тем же event_id ничего не дублирует.

        Возвращает True, если событие новое, False — если уже было.
        """
        with self.db.tx() as c:
            return self._event(c, event_type, task_id, payload,
                               event_id=event_id, correlation_id=correlation_id)

    def _event(self, c, event_type, task_id, payload=None, *, event_id=None,
               correlation_id=None):
        cur = c.execute(
            "INSERT OR IGNORE INTO events(event_id, task_id, event_type, timestamp,"
            " payload, correlation_id) VALUES (?,?,?,?,?,?)",
            (event_id or str(uuid.uuid4()), task_id, event_type, self.now(),
             canonical_json(payload or {}), correlation_id))
        return cur.rowcount == 1

    def events(self, task_id=None, event_type=None):
        sql, args = "SELECT * FROM events WHERE 1=1", []
        if task_id is not None:
            sql += " AND task_id = ?"
            args.append(task_id)
        if event_type is not None:
            sql += " AND event_type = ?"
            args.append(event_type)
        rows = self.conn.execute(sql + " ORDER BY id", args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out

    # ------------------------------------------------------------- CRUD ---

    def create_task(self, title, description="", *, priority=0, max_attempts=3,
                    idempotency_key=None, parent_task_id=None, depends_on=(),
                    required_capability=None, side_effects="none",
                    deadline_at=None):
        """Создаёт задачу в статусе PENDING.

        Если передан idempotency_key и задача с ним уже есть — возвращает её,
        ничего не создавая повторно. Тот же ключ с другими параметрами — ошибка.
        """
        depends_on = sorted(set(int(d) for d in depends_on))
        request = {"title": title, "description": description, "priority": priority,
                   "max_attempts": max_attempts, "parent_task_id": parent_task_id,
                   "depends_on": depends_on, "required_capability": required_capability,
                   "side_effects": side_effects, "deadline_at": deadline_at}
        request_hash = sha256(canonical_json(request))
        if side_effects not in ("none", "idempotent", "external"):
            raise ValidationError("side_effects должно быть none, idempotent или external")
        if not (1 <= int(max_attempts) <= 20):
            raise ValidationError("max_attempts должно быть от 1 до 20")
        with self.db.tx() as c:
            if idempotency_key is not None:
                existing = c.execute("SELECT * FROM tasks WHERE idempotency_key = ?",
                                     (idempotency_key,)).fetchone()
                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        raise IdempotencyConflictError(
                            f"idempotency_key {idempotency_key!r} уже использован с другими параметрами")
                    return dict(existing)
            if parent_task_id is not None:
                parent = self._get(c, parent_task_id)
                if parent["status"] in TERMINAL:
                    raise ValidationError("нельзя добавить подзадачу к завершённой задаче")
            now = self.now()
            cur = c.execute(
                "INSERT INTO tasks(title, description, status, priority, created_at,"
                " updated_at, available_at, max_attempts, idempotency_key, request_hash,"
                " parent_task_id, required_capability, side_effects, deadline_at)"
                " VALUES (?,?,'PENDING',?,?,?,?,?,?,?,?,?,?,?)",
                (title, description, priority, now, now, now, max_attempts,
                 idempotency_key, request_hash, parent_task_id, required_capability,
                 side_effects, deadline_at))
            task_id = cur.lastrowid
            self._event(c, "task_created", task_id, request)
            for dep in depends_on:
                self._add_dependency(c, task_id, dep)
            if parent_task_id is not None and parent["status"] in ("PENDING", "BLOCKED"):
                # Родитель ждёт своих подзадач.
                self._add_dependency(c, parent_task_id, task_id)
            return self._get(c, task_id)

    _UPDATABLE = {"title", "description", "priority", "max_attempts",
                  "deadline_at", "required_capability"}

    def update_task(self, task_id, **fields):
        """Меняет описательные поля. Статус так менять нельзя."""
        bad = set(fields) - self._UPDATABLE
        if bad:
            raise ValidationError(f"эти поля нельзя менять через update_task: {sorted(bad)}")
        if not fields:
            return self.get_task(task_id)
        with self.db.tx() as c:
            task = self._get(c, task_id)
            if task["status"] in TERMINAL:
                raise ValidationError("завершённую задачу менять нельзя")
            if "max_attempts" in fields:
                m = int(fields["max_attempts"])
                if not (max(1, task["attempt_count"]) <= m <= 20):
                    raise ValidationError("max_attempts вне допустимого диапазона")
            sets = ", ".join(f"{k} = ?" for k in sorted(fields))
            c.execute(f"UPDATE tasks SET {sets}, updated_at = ? WHERE id = ?",
                      [fields[k] for k in sorted(fields)] + [self.now(), task_id])
            self._event(c, "task_updated", task_id,
                        {"changes": {k: [task[k], fields[k]] for k in sorted(fields)}})
            return self._get(c, task_id)

    def get_task(self, task_id):
        return self._get(self.conn, task_id)

    def _get(self, c, task_id):
        r = c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if r is None:
            raise NotFoundError(f"задача {task_id} не найдена")
        return dict(r)

    def list_tasks(self, status=None):
        if status:
            rows = self.conn.execute("SELECT * FROM tasks WHERE status = ? ORDER BY id",
                                     (status,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def stats(self):
        rows = self.conn.execute("SELECT status, COUNT(*) n FROM tasks GROUP BY status")
        return {r["status"]: r["n"] for r in rows}

    def attempts(self, task_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM task_attempts WHERE task_id = ? ORDER BY attempt_number",
            (task_id,))]

    # ------------------------------------------------------ зависимости ---

    def add_dependency(self, task_id, depends_on_task_id):
        with self.db.tx() as c:
            task = self._get(c, task_id)
            if task["status"] not in ("PENDING", "BLOCKED"):
                raise ValidationError("зависимость можно добавить только задаче в PENDING или BLOCKED")
            self._add_dependency(c, task_id, depends_on_task_id)

    def _add_dependency(self, c, task_id, dep_id):
        if task_id == dep_id:
            raise DependencyCycleError("задача не может зависеть сама от себя")
        self._get(c, dep_id)
        cycle = c.execute(
            "WITH RECURSIVE reach(id) AS ("
            "  SELECT depends_on_task_id FROM task_dependencies WHERE task_id = :dep"
            "  UNION SELECT d.depends_on_task_id FROM task_dependencies d"
            "        JOIN reach r ON d.task_id = r.id)"
            " SELECT 1 FROM reach WHERE id = :task LIMIT 1",
            {"dep": dep_id, "task": task_id}).fetchone()
        if cycle:
            raise DependencyCycleError(f"зависимость {task_id} -> {dep_id} создаёт цикл")
        cur = c.execute("INSERT OR IGNORE INTO task_dependencies VALUES (?, ?)", (task_id, dep_id))
        if cur.rowcount:
            self._event(c, "dependency_added", task_id, {"depends_on": dep_id})

    def remove_dependency(self, task_id, depends_on_task_id):
        with self.db.tx() as c:
            cur = c.execute("DELETE FROM task_dependencies WHERE task_id = ? AND depends_on_task_id = ?",
                            (task_id, depends_on_task_id))
            if cur.rowcount:
                self._event(c, "dependency_removed", task_id, {"depends_on": depends_on_task_id})

    def _dep_statuses(self, c, task_id):
        return [dict(r) for r in c.execute(
            "SELECT t.id, t.status FROM task_dependencies d JOIN tasks t"
            " ON t.id = d.depends_on_task_id WHERE d.task_id = ?", (task_id,))]

    def dependencies_met(self, task_id):
        return all(d["status"] == "COMPLETED" for d in self._dep_statuses(self.conn, task_id))

    # -------------------------------------------------------- переходы ---

    def transition(self, task_id, new_status, *, reason=None, resume_condition=None,
                   expected_status=None):
        """Ручной переход состояния (с проверками). Переходы, связанные с
        попытками и проверкой результата, делаются только операциями ядра."""
        with self.db.tx() as c:
            task = self._get(c, task_id)
            if new_status in _INTERNAL_ONLY_TARGETS or task["status"] in _INTERNAL_ONLY_SOURCES:
                if is_allowed(task["status"], new_status):
                    raise InvalidTransitionError(
                        f"{task['status']} -> {new_status} выполняется только операцией ядра "
                        f"(claim/finish_attempt/verify/restart_failed/cancel)")
            return self._transition(c, task, new_status, reason=reason,
                                    resume_condition=resume_condition,
                                    expected_status=expected_status)

    def _transition(self, c, task, new_status, *, reason=None, resume_condition=None,
                    expected_status=None, extra=None, payload=None, correlation_id=None):
        """Единственное место, где меняется tasks.status."""
        src = task["status"]
        if new_status not in STATUSES:
            raise InvalidTransitionError(f"неизвестный статус {new_status}")
        if expected_status is not None and src != expected_status:
            raise InvalidTransitionError(f"ожидался статус {expected_status}, а сейчас {src}")
        if not is_allowed(src, new_status):
            raise InvalidTransitionError(f"переход {src} -> {new_status} запрещён")
        if new_status == "READY":
            unmet = [d for d in self._dep_statuses(c, task["id"]) if d["status"] != "COMPLETED"]
            if unmet:
                raise InvalidTransitionError(
                    f"READY невозможен: не завершены зависимости {[d['id'] for d in unmet]}")
        if new_status == "BLOCKED" and not reason:
            raise ValidationError("для BLOCKED нужна понятная причина (reason)")
        if new_status == "CANCELLED" and src in ("RUNNING",) and not task["cancel_requested"]:
            raise InvalidTransitionError("выполняющуюся задачу отменяет только cancel()")

        fields = dict(extra or {})
        fields["status"] = new_status
        fields["updated_at"] = self.now()
        if new_status == "BLOCKED":
            fields["blocked_reason"] = reason
            fields["resume_condition"] = resume_condition or "manual: разберитесь и вызовите resume() или cancel()"
        else:
            fields["blocked_reason"] = None
            fields["resume_condition"] = None
        sets = ", ".join(f"{k} = ?" for k in fields)
        cur = c.execute(f"UPDATE tasks SET {sets} WHERE id = ? AND status = ?",
                        list(fields.values()) + [task["id"], src])
        if cur.rowcount != 1:
            # Кто-то изменил задачу параллельно — не перезаписываем вслепую.
            raise InvalidTransitionError(f"задача {task['id']} уже не в статусе {src}")
        ev = {"from": src, "to": new_status}
        if reason:
            ev["reason"] = reason
        if payload:
            ev.update(payload)
        self._event(c, "status_changed", task["id"], ev, correlation_id=correlation_id)
        return self._get(c, task["id"])

    def promote_ready(self):
        """PENDING -> READY (если зависимости выполнены),
        RETRY_WAIT -> READY (если вышло время ожидания),
        PENDING -> BLOCKED (если зависимость отменена). Возвращает изменённые id."""
        changed = []
        with self.db.tx() as c:
            now = self.now()
            for t in [dict(r) for r in c.execute(
                    "SELECT * FROM tasks WHERE status = 'PENDING' ORDER BY id")]:
                deps = self._dep_statuses(c, t["id"])
                cancelled = [d["id"] for d in deps if d["status"] == "CANCELLED"]
                if cancelled:
                    self._transition(c, t, "BLOCKED",
                                     reason=f"зависимость отменена: {cancelled}",
                                     resume_condition="manual: удалите зависимость (remove_dependency) и вызовите resume(), либо отмените задачу")
                    changed.append(t["id"])
                elif all(d["status"] == "COMPLETED" for d in deps):
                    self._transition(c, t, "READY")
                    changed.append(t["id"])
            for t in [dict(r) for r in c.execute(
                    "SELECT * FROM tasks WHERE status = 'RETRY_WAIT' AND available_at <= ?"
                    " ORDER BY id", (now,))]:
                self._transition(c, t, "READY", reason="время ожидания повтора истекло")
                changed.append(t["id"])
        return changed

    # ------------------------------------------------------- провайдеры ---

    def register_provider(self, name, capabilities=(), *, enabled=True, quota_state=None):
        quota_state = quota_state or {}
        caps = list(capabilities)
        secrets = find_secrets({"capabilities": caps, "quota_state": quota_state})
        if secrets:
            raise SecretDetectedError(f"похоже на секрет, не сохраняю: {secrets}")
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO providers(provider_name, enabled, capabilities_json, quota_state_json)"
                " VALUES (?,?,?,?) ON CONFLICT(provider_name) DO UPDATE SET"
                " enabled = excluded.enabled, capabilities_json = excluded.capabilities_json,"
                " quota_state_json = excluded.quota_state_json",
                (name, int(enabled), canonical_json(caps), canonical_json(quota_state)))
            self._event(c, "provider_registered", None, {"provider": name, "capabilities": caps,
                                                          "enabled": bool(enabled)})

    def set_provider_status(self, name, *, enabled=None, unavailable_for=None, healthy=None):
        """Включить/выключить провайдера или пометить недоступным на N секунд."""
        with self.db.tx() as c:
            p = c.execute("SELECT * FROM providers WHERE provider_name = ?", (name,)).fetchone()
            if p is None:
                raise NotFoundError(f"провайдер {name} не найден")
            if enabled is not None:
                c.execute("UPDATE providers SET enabled = ? WHERE provider_name = ?",
                          (int(enabled), name))
            if unavailable_for is not None:
                until = iso(self.clock() + unavailable_for) if unavailable_for > 0 else None
                c.execute("UPDATE providers SET unavailable_until = ? WHERE provider_name = ?",
                          (until, name))
            if healthy is not None:
                c.execute("UPDATE providers SET last_health_check = ? WHERE provider_name = ?",
                          (self.now(), name))
            self._event(c, "provider_status", None, {"provider": name, "enabled": enabled,
                                                      "unavailable_for": unavailable_for,
                                                      "healthy": healthy})

    def select_provider(self, capability):
        """Возвращает имя доступного провайдера или бросает ProviderUnavailableError."""
        name = self._select_provider(self.conn, capability)
        if name is None:
            raise ProviderUnavailableError(f"нет доступного провайдера для {capability!r}")
        return name

    def _select_provider(self, c, capability):
        now = self.now()
        for p in c.execute("SELECT * FROM providers WHERE enabled = 1 AND"
                           " (unavailable_until IS NULL OR unavailable_until <= ?)"
                           " ORDER BY provider_name", (now,)):
            if capability in json.loads(p["capabilities_json"]):
                return p["provider_name"]
        return None

    # ---------------------------------------------------------- попытки ---

    def claim_next(self):
        """Атомарно берёт следующую READY-задачу и открывает попытку.
        Возвращает dict попытки (с ключом "task") или None, если брать нечего."""
        self.promote_ready()
        with self.db.tx() as c:
            now = self.now()
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM tasks WHERE status = 'READY' AND available_at <= ?"
                " ORDER BY priority DESC, available_at, id LIMIT 100", (now,))]
            for t in rows:
                if t["attempt_count"] >= t["max_attempts"]:
                    # Страховка: сюда не должны попадать, но бесконечный повтор недопустим.
                    self._transition(c, t, "BLOCKED", reason="лимит попыток исчерпан до запуска",
                                     resume_condition="manual: увеличьте max_attempts или отмените задачу")
                    continue
                provider = None
                if t["required_capability"]:
                    provider = self._select_provider(c, t["required_capability"])
                    if provider is None:
                        # Детерминированный event_id: одно событие на попытку, без спама.
                        self._event(c, "provider_unavailable", t["id"],
                                    {"capability": t["required_capability"]},
                                    event_id=f"provider_unavailable:{t['id']}:{t['restart_count']}:{t['attempt_count']}")
                        continue
                return self._open_attempt(c, t, provider)
        return None

    def _open_attempt(self, c, task, provider):
        number = c.execute("SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM task_attempts"
                           " WHERE task_id = ?", (task["id"],)).fetchone()[0]
        uid = str(uuid.uuid4())
        cp = self._latest_valid_checkpoint(c, task["id"])
        snapshot = {"title": task["title"], "description": task["description"],
                    "attempt_number": number,
                    "checkpoint_version": cp["checkpoint_version"] if cp else None}
        task = self._transition(c, task, "RUNNING",
                                extra={"attempt_count": task["attempt_count"] + 1,
                                       "assigned_provider": provider},
                                payload={"attempt_number": number, "provider": provider},
                                correlation_id=uid)
        cur = c.execute(
            "INSERT INTO task_attempts(task_id, attempt_number, attempt_uid, provider, status,"
            " started_at, lease_expires_at, input_snapshot) VALUES (?,?,?,?,'running',?,?,?)",
            (task["id"], number, uid, provider, self.now(),
             iso(self.clock() + self.lease_seconds), canonical_json(snapshot)))
        self._event(c, "attempt_started", task["id"], {"attempt_number": number,
                                                       "provider": provider},
                    correlation_id=uid)
        att = dict(c.execute("SELECT * FROM task_attempts WHERE id = ?", (cur.lastrowid,)).fetchone())
        att["task"] = task
        return att

    def heartbeat(self, attempt_id):
        """Продлевает аренду попытки (для долгих задач)."""
        with self.db.tx() as c:
            cur = c.execute("UPDATE task_attempts SET lease_expires_at = ? WHERE id = ?"
                            " AND status = 'running'",
                            (iso(self.clock() + self.lease_seconds), attempt_id))
            if cur.rowcount != 1:
                raise StaleAttemptError(f"попытка {attempt_id} уже не выполняется")

    def backoff_delay(self, attempt_count):
        """Экспоненциальная задержка с jitter: от 50% до 100% от base*2^(n-1)."""
        raw = min(self.backoff_cap, self.backoff_base * (2 ** max(0, attempt_count - 1)))
        return raw * (0.5 + 0.5 * self.rng())

    def finish_attempt(self, attempt_id, *, success, output=None, error=None,
                       error_kind="transient"):
        """Закрывает попытку. Повторный вызов с тем же исходом безопасен (ничего не меняет)."""
        if error_kind not in ("transient", "permanent", "needs_human"):
            raise ValidationError("error_kind: transient, permanent или needs_human")
        with self.db.tx() as c:
            att = c.execute("SELECT * FROM task_attempts WHERE id = ?", (attempt_id,)).fetchone()
            if att is None:
                raise NotFoundError(f"попытка {attempt_id} не найдена")
            att = dict(att)
            if att["status"] != "running":
                same = (success and att["status"] == "succeeded"
                        and att["output_snapshot"] == canonical_json(output)) or \
                       (not success and att["status"] == "failed" and att["error"] == str(error))
                if same:
                    return self._get(c, att["task_id"])
                stale = att["status"] == "interrupted"
        if att["status"] != "running":
            if stale:
                # Поздний результат от "зомби"-попытки: сохраняем в журнал для сверки,
                # но состояние не меняем.
                self.log_event("late_result_ignored", att["task_id"],
                               {"attempt_id": attempt_id, "success": success,
                                "output": output, "error": error},
                               event_id=f"late_result:{attempt_id}")
                raise StaleAttemptError(f"попытка {attempt_id} уже закрыта восстановлением")
            raise AttemptAlreadyFinishedError(f"попытка {attempt_id} уже завершена с другим исходом")

        with self.db.tx() as c:
            task = self._get(c, att["task_id"])
            now = self.now()
            if success:
                c.execute("UPDATE task_attempts SET status = 'succeeded', finished_at = ?,"
                          " output_snapshot = ? WHERE id = ? AND status = 'running'",
                          (now, canonical_json(output), attempt_id))
                self._event(c, "attempt_succeeded", task["id"],
                            {"attempt_number": att["attempt_number"]},
                            correlation_id=att["attempt_uid"])
                if task["cancel_requested"]:
                    return self._transition(c, task, "CANCELLED", reason="отмена запрошена во время выполнения")
                return self._transition(c, task, "VERIFYING", correlation_id=att["attempt_uid"])
            c.execute("UPDATE task_attempts SET status = 'failed', finished_at = ?, error = ?,"
                      " error_kind = ? WHERE id = ? AND status = 'running'",
                      (now, str(error), error_kind, attempt_id))
            self._event(c, "attempt_failed", task["id"],
                        {"attempt_number": att["attempt_number"], "error": str(error),
                         "kind": error_kind}, correlation_id=att["attempt_uid"])
            return self._handle_failure(c, task, error_kind, str(error),
                                        correlation_id=att["attempt_uid"])

    def _handle_failure(self, c, task, kind, error, correlation_id=None):
        if task["cancel_requested"]:
            return self._transition(c, task, "CANCELLED", reason="отмена запрошена во время выполнения",
                                    extra={"error_summary": error})
        if kind == "needs_human":
            return self._transition(c, task, "BLOCKED", reason=f"нужно вмешательство человека: {error}",
                                    resume_condition="manual: устраните причину и вызовите resume()",
                                    extra={"error_summary": error}, correlation_id=correlation_id)
        if kind == "permanent":
            return self._transition(c, task, "FAILED", reason=f"постоянная ошибка: {error}",
                                    extra={"error_summary": error}, correlation_id=correlation_id)
        if task["attempt_count"] >= task["max_attempts"]:
            return self._transition(
                c, task, "FAILED",
                reason=f"исчерпан лимит попыток ({task['max_attempts']})",
                extra={"error_summary": f"лимит попыток исчерпан; последняя ошибка: {error}"},
                correlation_id=correlation_id)
        delay = self.backoff_delay(task["attempt_count"])
        return self._transition(c, task, "RETRY_WAIT", reason=f"временная ошибка: {error}",
                                extra={"error_summary": error,
                                       "available_at": iso(self.clock() + delay)},
                                payload={"retry_in_seconds": round(delay, 3)},
                                correlation_id=correlation_id)

    def verify(self, task_id, *, passed, details=None, error_kind="transient"):
        """Принимает или отклоняет результат задачи в статусе VERIFYING."""
        with self.db.tx() as c:
            task = self._get(c, task_id)
            if task["status"] != "VERIFYING":
                raise InvalidTransitionError(f"проверять можно только VERIFYING, а сейчас {task['status']}")
            att = c.execute("SELECT * FROM task_attempts WHERE task_id = ? AND status = 'succeeded'"
                            " ORDER BY attempt_number DESC LIMIT 1", (task_id,)).fetchone()
            if passed:
                unfinished = [r["id"] for r in c.execute(
                    "SELECT id FROM tasks WHERE parent_task_id = ? AND status NOT IN"
                    " ('COMPLETED','CANCELLED')", (task_id,))]
                if unfinished:
                    raise ValidationError(f"нельзя завершить: не готовы подзадачи {unfinished}")
            result = {"passed": bool(passed), "details": details}
            if att is not None:
                c.execute("UPDATE task_attempts SET verification_result = ? WHERE id = ?",
                          (canonical_json(result), att["id"]))
            self._event(c, "verification", task_id, result)
            if passed:
                summary = details if isinstance(details, str) else canonical_json(details)
                return self._transition(c, task, "COMPLETED", extra={"result_summary": summary})
            return self._handle_failure(c, task, error_kind, f"проверка не пройдена: {details}")

    # ------------------------------------------------ ручные действия ---

    def cancel(self, task_id, reason="отменено пользователем"):
        """Отмена. Выполняющаяся задача отменяется после завершения текущей попытки."""
        with self.db.tx() as c:
            task = self._get(c, task_id)
            if task["status"] == "CANCELLED":
                return task
            if task["status"] == "COMPLETED":
                raise InvalidTransitionError("завершённую задачу отменить нельзя")
            if task["status"] == "RUNNING":
                if not task["cancel_requested"]:
                    c.execute("UPDATE tasks SET cancel_requested = 1, updated_at = ? WHERE id = ?",
                              (self.now(), task_id))
                    self._event(c, "cancel_requested", task_id, {"reason": reason})
                return self._get(c, task_id)
            result = self._transition(c, task, "CANCELLED", reason=reason)
            for child in [dict(r) for r in c.execute(
                    "SELECT id FROM tasks WHERE parent_task_id = ? AND status NOT IN"
                    " ('COMPLETED','CANCELLED')", (task_id,))]:
                self.cancel(child["id"], reason=f"отменена родительская задача {task_id}")
            return result

    def block(self, task_id, reason, resume_condition=None):
        return self.transition(task_id, "BLOCKED", reason=reason, resume_condition=resume_condition)

    def resume(self, task_id, note="возобновлено вручную"):
        """BLOCKED -> READY (если зависимости выполнены) или PENDING."""
        with self.db.tx() as c:
            task = self._get(c, task_id)
            if task["status"] != "BLOCKED":
                raise InvalidTransitionError(f"resume только из BLOCKED, а сейчас {task['status']}")
            deps_ok = all(d["status"] == "COMPLETED" for d in self._dep_statuses(c, task_id))
            extra = {"cancel_requested": 0}
            if task["attempt_count"] >= task["max_attempts"]:
                raise ValidationError("лимит попыток исчерпан: увеличьте max_attempts или используйте cancel()")
            return self._transition(c, task, "READY" if deps_ok else "PENDING", reason=note, extra=extra)

    def restart_failed(self, task_id, reason="ручной перезапуск"):
        """Явный ручной перезапуск FAILED: новый бюджет попыток, история сохраняется."""
        with self.db.tx() as c:
            task = self._get(c, task_id)
            if task["status"] != "FAILED":
                raise InvalidTransitionError(f"restart_failed только из FAILED, а сейчас {task['status']}")
            self._event(c, "manual_restart", task_id, {"reason": reason,
                                                      "previous_attempts": task["attempt_count"]})
            return self._transition(c, task, "PENDING", reason=reason,
                                    extra={"attempt_count": 0,
                                           "restart_count": task["restart_count"] + 1,
                                           "available_at": self.now()})

    # ---------------------------------------------------- checkpoints ---

    def save_checkpoint(self, task_id, state, artifact_refs=()):
        """Сохраняет новую версию контрольной точки (старые не трогаются)."""
        state_json = canonical_json(state)
        refs_json = canonical_json(list(artifact_refs))
        checksum = sha256(state_json + "\n" + refs_json)
        with self.db.tx() as c:
            self._get(c, task_id)
            version = c.execute("SELECT COALESCE(MAX(checkpoint_version), 0) + 1 FROM checkpoints"
                                " WHERE task_id = ?", (task_id,)).fetchone()[0]
            c.execute("INSERT INTO checkpoints(task_id, created_at, checkpoint_version, state_json,"
                      " artifact_refs, checksum) VALUES (?,?,?,?,?,?)",
                      (task_id, self.now(), version, state_json, refs_json, checksum))
            self._event(c, "checkpoint_saved", task_id, {"version": version, "checksum": checksum})
            return version

    def load_checkpoint(self, task_id, version=None):
        """Последняя ЦЕЛАЯ контрольная точка (повреждённые пропускаются и
        записываются в журнал). Если указана version — именно она или ошибка."""
        with self.db.tx() as c:
            if version is not None:
                r = c.execute("SELECT * FROM checkpoints WHERE task_id = ? AND checkpoint_version = ?",
                              (task_id, version)).fetchone()
                if r is None:
                    raise NotFoundError(f"checkpoint v{version} не найден")
                cp = self._check(c, dict(r))
                if cp is None:
                    raise CheckpointCorruptError(f"checkpoint v{version} задачи {task_id} повреждён")
                return cp
            return self._latest_valid_checkpoint(c, task_id)

    def _latest_valid_checkpoint(self, c, task_id):
        for r in c.execute("SELECT * FROM checkpoints WHERE task_id = ?"
                           " ORDER BY checkpoint_version DESC", (task_id,)).fetchall():
            cp = self._check(c, dict(r))
            if cp is not None:
                return cp
        return None

    def _check(self, c, r):
        problem = None
        try:
            if sha256(r["state_json"] + "\n" + r["artifact_refs"]) != r["checksum"]:
                problem = "checksum mismatch"
            else:
                r["state"] = json.loads(r["state_json"])
                r["artifact_refs"] = json.loads(r["artifact_refs"])
        except (TypeError, ValueError) as e:
            problem = f"unreadable: {e}"
        if problem:
            self._event(c, "checkpoint_corrupt", r["task_id"],
                        {"version": r["checkpoint_version"], "problem": problem},
                        event_id=f"checkpoint_corrupt:{r['id']}")
            return None
        return r

    # ------------------------------------------------------- artifacts ---

    def add_artifact(self, task_id, artifact_type, path_or_uri, *, content=None,
                     checksum=None, metadata=None):
        """Регистрирует артефакт (файл/ссылку). Повторная регистрация того же — без дублей."""
        if checksum is None:
            if content is None:
                raise ValidationError("нужен content или checksum")
            data = content if isinstance(content, bytes) else str(content).encode("utf-8")
            checksum = hashlib.sha256(data).hexdigest()
        with self.db.tx() as c:
            self._get(c, task_id)
            cur = c.execute("INSERT OR IGNORE INTO artifacts(task_id, artifact_type, path_or_uri,"
                            " checksum, created_at, metadata_json) VALUES (?,?,?,?,?,?)",
                            (task_id, artifact_type, path_or_uri, checksum, self.now(),
                             canonical_json(metadata or {})))
            if cur.rowcount:
                self._event(c, "artifact_added", task_id, {"path_or_uri": path_or_uri,
                                                           "checksum": checksum})
            return _row(c.execute("SELECT * FROM artifacts WHERE task_id = ? AND path_or_uri = ?"
                                  " AND checksum = ?", (task_id, path_or_uri, checksum)).fetchone())

    def artifacts(self, task_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM artifacts WHERE task_id = ? ORDER BY id", (task_id,))]

    # -------------------------------------------------------- recovery ---

    def recover(self, reconciler=None, *, force=False):
        """Восстановление после сбоя.

        force=True — при старте процесса: все "running"-попытки считаются
        прерванными (других процессов нет). Иначе — только с истёкшей арендой.

        reconciler(task, attempt, checkpoint) -> ("done", output) |
        ("not_done", None) | ("unknown", None) — проверка, было ли внешнее
        действие фактически выполнено. Без него задачи с side_effects="external"
        уходят в BLOCKED (а не повторяются вслепую).
        """
        report = []
        now = self.now()
        sql = "SELECT * FROM task_attempts WHERE status = 'running'"
        args = ()
        if not force:
            sql += " AND lease_expires_at < ?"
            args = (now,)
        for att in [dict(r) for r in self.conn.execute(sql + " ORDER BY id", args)]:
            report.append(self._recover_attempt(att, reconciler))
        # Задачи в RUNNING без открытой попытки (не должно быть, но проверяем).
        for t in [dict(r) for r in self.conn.execute(
                "SELECT * FROM tasks WHERE status = 'RUNNING' AND id NOT IN"
                " (SELECT task_id FROM task_attempts WHERE status = 'running')")]:
            with self.db.tx() as c:
                self._event(c, "recovery", t["id"], {"case": "running_without_attempt"})
                t = self._get(c, t["id"])
                if t["status"] == "RUNNING":
                    res = self._recover_task(c, t, "unknown" if t["side_effects"] == "external"
                                             else "not_done", None, None)
                    report.append({"task_id": t["id"], "attempt_id": None, "result": res["status"]})
        return report

    def _recover_attempt(self, att, reconciler):
        task = self.get_task(att["task_id"])
        checkpoint = self.load_checkpoint(task["id"])
        outcome, output = None, None
        if reconciler is not None:
            try:
                outcome, output = reconciler(task, att, checkpoint)
            except Exception as e:  # сверка сама упала -> безопасный вариант
                outcome = "unknown"
                output = None
                self.log_event("reconcile_error", task["id"], {"error": str(e)})
        elif task["side_effects"] == "external":
            outcome = "unknown"
        else:
            outcome = "not_done"
        if outcome not in ("done", "not_done", "unknown"):
            outcome = "unknown"
        with self.db.tx() as c:
            fresh = c.execute("SELECT status FROM task_attempts WHERE id = ?", (att["id"],)).fetchone()
            if fresh["status"] != "running":  # уже обработано
                return {"task_id": task["id"], "attempt_id": att["id"], "result": "skipped"}
            self._event(c, "recovery", task["id"],
                        {"attempt_id": att["id"], "attempt_number": att["attempt_number"],
                         "outcome": outcome,
                         "checkpoint_version": checkpoint["checkpoint_version"] if checkpoint else None},
                        event_id=f"recovery:{att['id']}", correlation_id=att["attempt_uid"])
            if outcome == "done":
                c.execute("UPDATE task_attempts SET status = 'succeeded', finished_at = ?,"
                          " output_snapshot = ? WHERE id = ?",
                          (self.now(), canonical_json(output), att["id"]))
            else:
                c.execute("UPDATE task_attempts SET status = 'interrupted', finished_at = ?,"
                          " error = ?, error_kind = 'interrupted' WHERE id = ?",
                          (self.now(), "процесс прервался во время попытки", att["id"]))
            task = self._get(c, task["id"])
            res = self._recover_task(c, task, outcome, output, att)
        return {"task_id": task["id"], "attempt_id": att["id"], "result": res["status"]}

    def _recover_task(self, c, task, outcome, output, att):
        cid = att["attempt_uid"] if att else None
        if task["status"] != "RUNNING":
            return task
        if outcome == "done":
            if task["cancel_requested"]:
                return self._transition(c, task, "CANCELLED", reason="отмена (после восстановления)")
            return self._transition(c, task, "VERIFYING", reason="восстановлено: действие подтверждено сверкой",
                                    correlation_id=cid)
        if outcome == "unknown":
            # Даже при запрошенной отмене: сначала человек должен выяснить,
            # произошло ли внешнее действие.
            return self._transition(
                c, task, "BLOCKED",
                reason="процесс прервался во время внешнего действия; неизвестно, выполнено ли оно",
                resume_condition="manual: проверьте фактический результат; если не выполнено — resume(), "
                                 "если выполнено — cancel() или ручное завершение",
                correlation_id=cid)
        return self._handle_failure(c, task, "transient", "процесс прервался во время попытки",
                                    correlation_id=cid)

    # ----------------------------------------------------------- worker ---

    def run_once(self, executor, verifier=None):
        """Один шаг исполнителя. Возвращает описание шага или None, если делать нечего.

        executor(task, ctx) -> output (JSON-совместимый). Может бросать
        TransientError / PermanentError / NeedsHumanError.
        verifier(task, output) -> (passed: bool, details).
        """
        verifier = verifier or basic_verifier
        self.promote_ready()
        pending = self.conn.execute("SELECT id FROM tasks WHERE status = 'VERIFYING'"
                                    " ORDER BY id LIMIT 1").fetchone()
        if pending:
            return {"action": "verified", **self._run_verifier(pending["id"], verifier)}
        att = self.claim_next()
        if att is None:
            return None
        ctx = AttemptContext(self, att)
        try:
            output = executor(att["task"], ctx)
        except ExecutorError as e:
            t = self.finish_attempt(att["id"], success=False, error=str(e), error_kind=e.kind)
            return {"action": "failed", "task_id": t["id"], "status": t["status"]}
        except Exception as e:  # неизвестная ошибка = временная, но с лимитом попыток
            t = self.finish_attempt(att["id"], success=False, error=f"{type(e).__name__}: {e}")
            return {"action": "failed", "task_id": t["id"], "status": t["status"]}
        t = self.finish_attempt(att["id"], success=True, output=output)
        if t["status"] == "VERIFYING":
            return {"action": "executed", **self._run_verifier(t["id"], verifier)}
        return {"action": "executed", "task_id": t["id"], "status": t["status"]}

    def _run_verifier(self, task_id, verifier):
        task = self.get_task(task_id)
        att = self.conn.execute("SELECT output_snapshot FROM task_attempts WHERE task_id = ? AND"
                                " status = 'succeeded' ORDER BY attempt_number DESC LIMIT 1",
                                (task_id,)).fetchone()
        output = json.loads(att["output_snapshot"]) if att and att["output_snapshot"] else None
        try:
            passed, details = verifier(task, output)
        except Exception as e:
            passed, details = False, f"ошибка проверяющего: {e}"
        try:
            t = self.verify(task_id, passed=passed, details=details)
        except ValidationError as e:  # например, подзадачи ещё не готовы
            # Не крутимся в цикле: безопасно паркуем задачу с понятной причиной.
            with self.db.tx() as c:
                t = self._transition(c, self._get(c, task_id), "BLOCKED", reason=str(e),
                                     resume_condition="manual: дождитесь подзадач и вызовите resume()")
        return {"task_id": t["id"], "status": t["status"]}

    def run_until_idle(self, executor, verifier=None, max_steps=1000):
        """Крутит run_once, пока есть работа, но не больше max_steps (защита от зацикливания)."""
        steps = []
        for _ in range(max_steps):
            step = self.run_once(executor, verifier)
            if step is None:
                return steps
            steps.append(step)
        self.log_event("worker_step_limit", None, {"max_steps": max_steps})
        return steps


class AttemptContext:
    """То, что исполнитель видит во время попытки."""

    def __init__(self, core, attempt):
        self.core = core
        self.attempt = attempt
        self.task_id = attempt["task_id"]

    @property
    def last_checkpoint(self):
        return self.core.load_checkpoint(self.task_id)

    def checkpoint(self, state, artifact_refs=()):
        return self.core.save_checkpoint(self.task_id, state, artifact_refs)

    def heartbeat(self):
        self.core.heartbeat(self.attempt["id"])

    def cancel_requested(self):
        return bool(self.core.get_task(self.task_id)["cancel_requested"])


def basic_verifier(task, output):
    """Минимальная проверка этапа A: результат должен существовать и не быть пустым."""
    if output is None or output == "" or output == {} or output == []:
        return False, "пустой результат"
    return True, "результат получен"

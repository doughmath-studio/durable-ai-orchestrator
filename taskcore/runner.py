"""Этап B: один «такт» системы. Запускается по расписанию GitHub Actions.

Такт: восстановление после сбоя -> новые Issues становятся задачами ->
исполнитель делает задачи -> итог пишется комментарием в Issue.
Всё состояние в SQLite, поэтому такт можно оборвать в любой момент:
следующий продолжит с того же места.
"""
import json

from .errors import IdempotencyConflictError

KEY_PREFIX = "gh-issue-"
MAX_PROMPT_CHARS = 8000
REPORTED_STATUSES = ("COMPLETED", "FAILED", "BLOCKED")


def issue_number(task):
    key = task.get("idempotency_key") or ""
    return int(key[len(KEY_PREFIX):]) if key.startswith(KEY_PREFIX) else None


def make_executor(ask):
    """Исполнитель: отдаёт текст задачи нейросети, возвращает её ответ."""
    def executor(task, ctx):
        prompt = task["title"]
        if task["description"]:
            prompt += "\n\n" + task["description"]
        answer = ask(prompt[:MAX_PROMPT_CHARS])
        return {"answer": answer}
    return executor


def answer_verifier(task, output):
    answer = (output or {}).get("answer") if isinstance(output, dict) else None
    if not isinstance(answer, str) or not answer.strip():
        return False, "нейросеть вернула пустой ответ"
    return True, "ответ получен"


def sync_inbox(core, gh, allowed_authors):
    """Открытые Issues от разрешённых авторов -> задачи (повторно не создаются)."""
    created = []
    for issue in gh.open_issues():
        if issue.get("user", {}).get("login") not in allowed_authors:
            continue
        key = f"{KEY_PREFIX}{issue['number']}"
        if core.conn.execute("SELECT 1 FROM tasks WHERE idempotency_key = ?", (key,)).fetchone():
            continue
        try:
            task = core.create_task(issue["title"], issue.get("body") or "",
                                    idempotency_key=key, max_attempts=3)
        except IdempotencyConflictError:
            continue  # Issue отредактировали после создания задачи: работаем с исходной версией
        created.append(task["id"])
    return created


RETRY_WORDS = ("повтор", "retry")


def sync_retries(core, gh, allowed_authors):
    """Комментарий владельца «повтори» в открытом Issue перезапускает FAILED/BLOCKED задачу.

    Каждый комментарий срабатывает один раз (event_id = id комментария).
    """
    retried = []
    open_numbers = {i["number"] for i in gh.open_issues()}
    for status in ("FAILED", "BLOCKED"):
        for task in core.list_tasks(status):
            number = issue_number(task)
            if number is None or number not in open_numbers:
                continue
            for c in gh.comments(number):
                if c.get("user", {}).get("login") not in allowed_authors:
                    continue
                if "<!-- taskcore:" in (c.get("body") or ""):
                    continue
                if not any(w in (c.get("body") or "").lower() for w in RETRY_WORDS):
                    continue
                if not core.log_event("github_retry_request", task["id"],
                                      {"issue": number, "comment_id": c.get("id")},
                                      event_id=f"gh-retry:{c.get('id')}"):
                    continue  # этот комментарий уже обработан
                if status == "FAILED":
                    core.restart_failed(task["id"], "повтор по комментарию в Issue")
                else:
                    core.resume(task["id"], "возобновлено по комментарию в Issue")
                retried.append(task["id"])
                break
    return retried


def _report_text(core, task):
    if task["status"] == "COMPLETED":
        out = None
        for att in reversed(core.attempts(task["id"])):
            if att["status"] == "succeeded" and att["output_snapshot"]:
                out = json.loads(att["output_snapshot"])
                break
        answer = (out or {}).get("answer", "(ответ не найден)")
        return f"{answer}\n\n---\n_Задача #{task['id']} выполнена автоматически._"
    if task["status"] == "FAILED":
        attempts = core.attempts(task["id"])
        att = attempts[-1] if attempts else {}
        return (f"Не получилось после {task['attempt_count']} попыток.\n\n"
                f"Последняя ошибка: {att.get('error') or 'неизвестно'}\n\n"
                f"---\n_Задача #{task['id']}._")
    return (f"Задача остановлена и ждёт человека.\n\nПричина: {task['blocked_reason']}\n\n"
            f"---\n_Задача #{task['id']}._")


def sync_outbox(core, gh):
    """Итог каждой задачи пишется в её Issue ровно один раз, даже после сбоя."""
    reported = []
    for status in REPORTED_STATUSES:
        for task in core.list_tasks(status):
            number = issue_number(task)
            if number is None:
                continue
            key = (f"gh-report:{task['id']}:{task['status']}:"
                   f"{task['attempt_count']}:{task['restart_count']}")
            if any(e["event_id"] == key for e in core.events(task["id"], "github_reported")):
                continue
            marker = f"<!-- taskcore:{key} -->"
            # Сверка: комментарий мог уйти, а запись в базу — нет (сбой между ними).
            if not any(marker in (c.get("body") or "") for c in gh.comments(number)):
                gh.add_comment(number, _report_text(core, task) + "\n" + marker)
            if task["status"] == "COMPLETED":
                gh.close_issue(number)
            core.log_event("github_reported", task["id"], {"issue": number, "status": task["status"]},
                           event_id=key)
            reported.append(task["id"])
    return reported


def tick(core, gh, ask, allowed_authors, *, max_steps=20):
    """Один такт. Возвращает отчёт для журнала GitHub Actions."""
    report = {"recovered": core.recover(force=True)}
    report["new_tasks"] = sync_inbox(core, gh, set(allowed_authors))
    report["retried"] = sync_retries(core, gh, set(allowed_authors))
    if ask is None:
        # Нет ни одного ключа: задачи ждут и попытки не тратят.
        report["steps"] = []
        report["waiting"] = "нет ключа нейросети (секрет GROQ_API_KEY или HF_API_KEY)"
    else:
        report["steps"] = core.run_until_idle(make_executor(ask), answer_verifier,
                                              max_steps=max_steps)
    report["errors"] = [
        {"task_id": s["task_id"], "error": core.attempts(s["task_id"])[-1]["error"]}
        for s in report["steps"] if s.get("action") == "failed"]
    report["reported"] = sync_outbox(core, gh)
    report["stats"] = core.stats()
    return report

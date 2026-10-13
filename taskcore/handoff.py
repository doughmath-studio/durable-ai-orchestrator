"""Пакет передачи задачи другому ИИ (handoff packet).

Всё берётся из базы, а не из памяти модели. Секреты вычищаются.
"""
import json

from .core import _SECRET_KEY, _SECRET_VALUE

FIELDS = ["project_id", "task_id", "task_goal", "acceptance_criteria", "source_commit",
          "current_state", "completed_steps", "pending_steps", "decisions_and_rationale",
          "changed_files", "test_commands", "test_results", "known_errors", "risks",
          "constraints", "artifact_references", "exact_next_action", "required_output_format"]


def redact(obj):
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if _SECRET_KEY.search(str(k)) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        return _SECRET_VALUE.sub("[REDACTED]", obj)
    return obj


def build_handoff(core, task_id, *, project_id="future", source_commit=None, **overrides):
    """Собирает пакет. Поля, которых нет в checkpoint, можно передать через overrides.

    Checkpoint задачи может содержать ключи с теми же именами
    (completed_steps, pending_steps, decisions_and_rationale, ...).
    """
    task = core.get_task(task_id)
    cp = core.load_checkpoint(task_id)
    state = (cp or {}).get("state") or {}
    failed = [a for a in core.attempts(task_id) if a["error"]]
    packet = {
        "project_id": project_id,
        "task_id": task_id,
        "task_goal": f"{task['title']}. {task['description']}".strip(),
        "acceptance_criteria": state.get("acceptance_criteria", []),
        "source_commit": source_commit or "нет репозитория (NOT SET)",
        "current_state": {"status": task["status"], "attempt_count": task["attempt_count"],
                          "max_attempts": task["max_attempts"],
                          "blocked_reason": task["blocked_reason"],
                          "resume_condition": task["resume_condition"],
                          "checkpoint_version": cp["checkpoint_version"] if cp else None},
        "completed_steps": state.get("completed_steps", []),
        "pending_steps": state.get("pending_steps", []),
        "decisions_and_rationale": state.get("decisions_and_rationale", []),
        "changed_files": state.get("changed_files", []),
        "test_commands": state.get("test_commands", ["python -m pytest -v"]),
        "test_results": state.get("test_results", "NOT RUN"),
        "known_errors": [a["error"] for a in failed][-5:],
        "risks": state.get("risks", []),
        "constraints": state.get("constraints", [
            "бюджет 0: никаких платных API без разрешения владельца",
            "управление только с iPhone/Safari",
            "не передавать и не хранить секреты"]),
        "artifact_references": [a["path_or_uri"] for a in core.artifacts(task_id)],
        "exact_next_action": state.get("exact_next_action", "уточнить по current_state"),
        "required_output_format": state.get("required_output_format", "JSON"),
    }
    for k, v in overrides.items():
        if k not in FIELDS:
            raise ValueError(f"неизвестное поле пакета: {k}")
        packet[k] = v
    return redact(packet)


def handoff_to_markdown(packet):
    lines = [f"# HANDOFF — {packet['project_id']} / задача {packet['task_id']}", ""]
    for k in FIELDS:
        v = packet[k]
        if isinstance(v, (dict, list)):
            v = "\n```json\n" + json.dumps(v, ensure_ascii=False, indent=2) + "\n```"
        lines.append(f"- **{k}**: {v}")
    lines.append("")
    lines.append("> Секреты, пароли и API-ключи в пакет не включаются.")
    return "\n".join(lines)

"""Командная строка: python -m taskcore <команда>.

База по умолчанию: data/taskcore.db (можно поменять переменной TASKCORE_DB).
"""
import argparse
import json
import os
import sys
import tempfile

from .core import TaskCore
from .handoff import build_handoff, handoff_to_markdown


def _core():
    return TaskCore(os.environ.get("TASKCORE_DB", "data/taskcore.db"))


def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def demo(db_path=None, quiet=False):
    """Демонстрация полного цикла без внешних сервисов. Возвращает True, если всё как ожидалось."""
    own_tmp = db_path is None
    if own_tmp:
        tmp = tempfile.mkdtemp()
        db_path = os.path.join(tmp, "demo.db")
    say = (lambda *a: None) if quiet else print
    core = TaskCore(db_path, backoff_base=0.0)
    parent = core.create_task("Написать отчёт", idempotency_key="demo-report")
    a = core.create_task("Собрать данные", parent_task_id=parent["id"])
    b = core.create_task("Сделать выводы", parent_task_id=parent["id"], depends_on=[a["id"]])
    calls = {"n": 0}

    def executor(task, ctx):
        if task["id"] == a["id"]:
            calls["n"] += 1
            if calls["n"] == 1:
                from .errors import TransientError
                raise TransientError("сеть временно недоступна (имитация)")
        ctx.checkpoint({"completed_steps": [task["title"]]})
        return {"result": f"готово: {task['title']}"}

    steps = core.run_until_idle(executor, max_steps=50)
    for s in steps:
        say(s)
    # Имитация сбоя: задача взята в работу, и процесс "упал".
    c = core.create_task("Задача, прерванная сбоем")
    core.claim_next()
    core.close()
    core = TaskCore(db_path, backoff_base=0.0)
    say("recover:", core.recover(force=True))
    core.run_until_idle(executor, max_steps=50)
    st = core.stats()
    say("итог:", st, "событий в журнале:", len(core.events()))
    ok = (core.get_task(parent["id"])["status"] == "COMPLETED"
          and core.get_task(b["id"])["status"] == "COMPLETED"
          and core.get_task(c["id"])["status"] == "COMPLETED"
          and calls["n"] == 2)
    core.close()
    return ok


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m taskcore", description="Durable Task Core")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="создать/обновить базу")
    a = sub.add_parser("add", help="добавить задачу")
    a.add_argument("title")
    a.add_argument("--description", default="")
    a.add_argument("--priority", type=int, default=0)
    a.add_argument("--max-attempts", type=int, default=3)
    a.add_argument("--key", help="idempotency key")
    a.add_argument("--depends-on", type=int, nargs="*", default=[])
    l = sub.add_parser("list", help="список задач")
    l.add_argument("--status")
    s = sub.add_parser("show", help="задача, попытки и события")
    s.add_argument("id", type=int)
    for name in ("cancel", "resume", "restart"):
        x = sub.add_parser(name)
        x.add_argument("id", type=int)
        x.add_argument("--reason", default=None)
    b = sub.add_parser("block")
    b.add_argument("id", type=int)
    b.add_argument("reason")
    sub.add_parser("recover", help="восстановление после сбоя (запускать при старте)")
    sub.add_parser("stats")
    h = sub.add_parser("handoff", help="пакет передачи задачи другому ИИ")
    h.add_argument("id", type=int)
    sub.add_parser("demo", help="демонстрация на временной базе")
    args = p.parse_args(argv)

    if args.cmd == "demo":
        ok = demo()
        print("DEMO OK" if ok else "DEMO FAILED")
        return 0 if ok else 1
    core = _core()
    if args.cmd == "init":
        print(f"база готова: {core.db.path}, версия схемы {core.db.schema_version}")
    elif args.cmd == "add":
        _print(core.create_task(args.title, args.description, priority=args.priority,
                                max_attempts=args.max_attempts, idempotency_key=args.key,
                                depends_on=args.depends_on))
    elif args.cmd == "list":
        for t in core.list_tasks(args.status):
            print(f"#{t['id']:<4} {t['status']:<10} попыток {t['attempt_count']}/{t['max_attempts']}  {t['title']}")
    elif args.cmd == "show":
        _print({"task": core.get_task(args.id), "attempts": core.attempts(args.id),
                "events": core.events(args.id)})
    elif args.cmd == "cancel":
        _print(core.cancel(args.id, args.reason or "отменено пользователем"))
    elif args.cmd == "resume":
        _print(core.resume(args.id, args.reason or "возобновлено вручную"))
    elif args.cmd == "restart":
        _print(core.restart_failed(args.id, args.reason or "ручной перезапуск"))
    elif args.cmd == "block":
        _print(core.block(args.id, args.reason))
    elif args.cmd == "recover":
        _print(core.recover(force=True))
    elif args.cmd == "stats":
        _print(core.stats())
    elif args.cmd == "handoff":
        print(handoff_to_markdown(build_handoff(core, args.id)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

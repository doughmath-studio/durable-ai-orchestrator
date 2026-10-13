"""Тесты Durable Task Core. Номера в именах соответствуют списку сценариев в ТЗ (раздел 10).
Подробное описание каждого теста — в docs/TESTS.md."""
import json
import sqlite3

import pytest

from taskcore import (
    AttemptAlreadyFinishedError, CheckpointCorruptError, DependencyCycleError,
    IdempotencyConflictError, InvalidTransitionError, NeedsHumanError,
    PermanentError, ProviderUnavailableError, SecretDetectedError,
    StaleAttemptError, TransientError, ValidationError, TRANSITIONS,
    build_handoff, handoff_to_markdown,
)
from conftest import ok_executor


def statuses(core, task_id):
    return [(e["payload"]["from"], e["payload"]["to"])
            for e in core.events(task_id, "status_changed")]


# 1
def test_01_create_task(core):
    t = core.create_task("Первая задача", "описание", priority=5)
    assert t["status"] == "PENDING"
    assert t["priority"] == 5 and t["attempt_count"] == 0 and t["max_attempts"] == 3
    assert core.get_task(t["id"])["title"] == "Первая задача"
    assert [e["event_type"] for e in core.events(t["id"])] == ["task_created"]


# 2
def test_02_update_task(core):
    t = core.create_task("Старое имя")
    t2 = core.update_task(t["id"], title="Новое имя", priority=9)
    assert t2["title"] == "Новое имя" and t2["priority"] == 9
    ev = core.events(t["id"], "task_updated")[0]
    assert ev["payload"]["changes"]["title"] == ["Старое имя", "Новое имя"]
    with pytest.raises(ValidationError):
        core.update_task(t["id"], status="COMPLETED")  # статус так не меняется


# 3
def test_03_invalid_transition_rejected(core):
    t = core.create_task("x")
    with pytest.raises(InvalidTransitionError):
        core.transition(t["id"], "COMPLETED")
    with pytest.raises(InvalidTransitionError):
        core.transition(t["id"], "RUNNING")
    with pytest.raises(InvalidTransitionError):
        core.transition(t["id"], "NO_SUCH_STATUS")
    assert core.get_task(t["id"])["status"] == "PENDING"
    assert statuses(core, t["id"]) == []
    # Терминальные состояния действительно терминальные.
    assert TRANSITIONS["COMPLETED"] == set() and TRANSITIONS["CANCELLED"] == set()


# 4
def test_04_dependency_on_unfinished_task(core):
    a = core.create_task("A")
    b = core.create_task("B", depends_on=[a["id"]])
    with pytest.raises(InvalidTransitionError):
        core.transition(b["id"], "READY")
    core.promote_ready()
    assert core.get_task(a["id"])["status"] == "READY"
    assert core.get_task(b["id"])["status"] == "PENDING"


# 5
def test_05_ready_after_dependency_completed(core):
    a = core.create_task("A")
    b = core.create_task("B", depends_on=[a["id"]])
    step = core.run_once(ok_executor)
    assert step["task_id"] == a["id"] and step["status"] == "COMPLETED"
    core.promote_ready()
    assert core.get_task(b["id"])["status"] == "READY"


# 6
def test_06_successful_execution(core):
    t = core.create_task("ok")
    step = core.run_once(ok_executor)
    assert step["status"] == "COMPLETED"
    assert statuses(core, t["id"]) == [("PENDING", "READY"), ("READY", "RUNNING"),
                                       ("RUNNING", "VERIFYING"), ("VERIFYING", "COMPLETED")]
    att = core.attempts(t["id"])
    assert len(att) == 1 and att[0]["status"] == "succeeded"
    assert json.loads(att[0]["verification_result"])["passed"] is True
    assert core.run_once(ok_executor) is None


# 7
def test_07_error_then_retry_with_backoff(core, clock):
    t = core.create_task("flaky", max_attempts=3)
    calls = []

    def ex(task, ctx):
        calls.append(1)
        if len(calls) == 1:
            raise TransientError("timeout")
        return {"ok": True}

    assert core.run_once(ex)["status"] == "RETRY_WAIT"
    assert core.get_task(t["id"])["error_summary"] == "timeout"
    assert core.run_once(ex) is None            # ещё рано: идёт backoff
    clock.advance(10.0)                          # base=10, rng=1.0 -> задержка 10 c
    assert core.run_once(ex)["status"] == "COMPLETED"
    assert len(calls) == 2 and core.get_task(t["id"])["attempt_count"] == 2


def test_07b_backoff_grows_and_has_jitter(make_core):
    core = make_core(rng=lambda: 0.0, backoff_base=10.0, backoff_cap=50.0)
    assert [core.backoff_delay(n) for n in (1, 2, 3, 4, 5)] == [5.0, 10.0, 20.0, 25.0, 25.0]
    core.rng = lambda: 1.0
    assert [core.backoff_delay(n) for n in (1, 2, 3, 4)] == [10.0, 20.0, 40.0, 50.0]


# 8
def test_08_max_attempts_reached(core, clock):
    t = core.create_task("always fails", max_attempts=2)

    def ex(task, ctx):
        raise TransientError("boom")

    assert core.run_once(ex)["status"] == "RETRY_WAIT"
    clock.advance(1000)
    assert core.run_once(ex)["status"] == "FAILED"
    t = core.get_task(t["id"])
    assert t["attempt_count"] == 2 and "лимит попыток" in t["error_summary"]
    clock.advance(10_000)
    assert core.run_once(ex) is None


# 9
def test_09_restart_after_unfinished_running(make_core, clock):
    core = make_core()
    t = core.create_task("будет прервана")
    att = core.claim_next()
    assert core.get_task(t["id"])["status"] == "RUNNING"
    core.close()                               # "процесс упал"

    core2 = make_core()
    report = core2.recover(force=True)
    assert report == [{"task_id": t["id"], "attempt_id": att["id"], "result": "RETRY_WAIT"}]
    assert core2.attempts(t["id"])[0]["status"] == "interrupted"
    assert len(core2.events(t["id"], "recovery")) == 1
    clock.advance(100)
    assert core2.run_once(ok_executor)["status"] == "COMPLETED"


def test_09b_recover_only_expired_leases(make_core, clock):
    core = make_core(lease_seconds=60)
    t = core.create_task("долгая")
    att = core.claim_next()
    assert core.recover() == []                # аренда ещё действует
    clock.advance(30)
    core.heartbeat(att["id"])                  # продлили
    clock.advance(45)
    assert core.recover() == []
    clock.advance(30)
    assert core.recover()[0]["task_id"] == t["id"]


# 10
def test_10_checkpoint_save_and_load(core):
    t = core.create_task("c")
    assert core.load_checkpoint(t["id"]) is None
    v1 = core.save_checkpoint(t["id"], {"step": 1}, ["file://a"])
    v2 = core.save_checkpoint(t["id"], {"step": 2, "текст": "юникод"})
    assert (v1, v2) == (1, 2)
    cp = core.load_checkpoint(t["id"])
    assert cp["checkpoint_version"] == 2 and cp["state"] == {"step": 2, "текст": "юникод"}
    assert core.load_checkpoint(t["id"], version=1)["artifact_refs"] == ["file://a"]


# 11
def test_11_idempotent_create(core):
    a = core.create_task("same", idempotency_key="k1")
    b = core.create_task("same", idempotency_key="k1")
    assert a["id"] == b["id"]
    assert len(core.list_tasks()) == 1
    assert len(core.events(a["id"], "task_created")) == 1
    with pytest.raises(IdempotencyConflictError):
        core.create_task("другое", idempotency_key="k1")


def test_11b_finish_attempt_is_idempotent(core):
    t = core.create_task("x")
    att = core.claim_next()
    r1 = core.finish_attempt(att["id"], success=True, output={"v": 1})
    r2 = core.finish_attempt(att["id"], success=True, output={"v": 1})  # повтор того же вызова
    assert r1["status"] == r2["status"] == "VERIFYING"
    assert len(core.events(t["id"], "attempt_succeeded")) == 1
    with pytest.raises(AttemptAlreadyFinishedError):
        core.finish_attempt(att["id"], success=False, error="другое")


# 12
def test_12_corrupted_checkpoint(core):
    t = core.create_task("c")
    core.save_checkpoint(t["id"], {"step": 1})
    core.save_checkpoint(t["id"], {"step": 2})
    # Портим v2 в обход API (имитация повреждения диска/ручной правки).
    core.conn.execute("UPDATE checkpoints SET state_json = '{\"step\": 99' WHERE checkpoint_version = 2")
    cp = core.load_checkpoint(t["id"])
    assert cp["checkpoint_version"] == 1 and cp["state"] == {"step": 1}
    with pytest.raises(CheckpointCorruptError):
        core.load_checkpoint(t["id"], version=2)
    core.load_checkpoint(t["id"])
    assert len(core.events(t["id"], "checkpoint_corrupt")) == 1   # без дублей в журнале
    # Если повреждены все версии — None, а не мусор.
    core.conn.execute("UPDATE checkpoints SET checksum = 'bad'")
    assert core.load_checkpoint(t["id"]) is None


# 13
def test_13_sqlite_transaction_error_rolls_back(core, db_path, monkeypatch):
    t = core.create_task("x")
    core.promote_ready()
    events_before = len(core.events())

    original = core._event

    def broken_event(c, event_type, *a, **kw):
        if event_type == "status_changed":
            raise sqlite3.OperationalError("disk I/O error (имитация)")
        return original(c, event_type, *a, **kw)

    monkeypatch.setattr(core, "_event", broken_event)
    with pytest.raises(sqlite3.OperationalError):
        core.claim_next()
    monkeypatch.undo()
    assert core.get_task(t["id"])["status"] == "READY"       # статус не изменился
    assert core.attempts(t["id"]) == []                       # попытка не создана
    assert len(core.events()) == events_before                # событий не прибавилось

    # Блокировка базы другим процессом: операция падает целиком, ничего не пишет.
    other = sqlite3.connect(db_path, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    core.db.conn.execute("PRAGMA busy_timeout = 50")
    with pytest.raises(sqlite3.OperationalError):
        core.create_task("не должна появиться")
    other.execute("ROLLBACK")
    other.close()
    assert [x["title"] for x in core.list_tasks()] == ["x"]
    # После ошибки ядро продолжает работать.
    assert core.run_once(ok_executor)["status"] == "COMPLETED"


# 14
def test_14_duplicate_events_ignored(core):
    t = core.create_task("x")
    assert core.log_event("external_callback", t["id"], {"n": 1}, event_id="evt-1") is True
    assert core.log_event("external_callback", t["id"], {"n": 1}, event_id="evt-1") is False
    assert len(core.events(t["id"], "external_callback")) == 1
    # Журнал неизменяем на уровне базы.
    with pytest.raises(sqlite3.DatabaseError):
        core.conn.execute("UPDATE events SET event_type = 'hacked'")
    with pytest.raises(sqlite3.DatabaseError):
        core.conn.execute("DELETE FROM events")


# 15
def test_15_recovery_after_partial_execution(make_core, clock):
    core = make_core()
    parent = core.create_task("parent")
    a = core.create_task("A", parent_task_id=parent["id"])
    b = core.create_task("B", parent_task_id=parent["id"])
    assert core.run_once(ok_executor)["task_id"] == a["id"]     # A завершена
    att = core.claim_next()                                      # B начата...
    assert att["task_id"] == b["id"]
    ctx_cp = core.save_checkpoint(b["id"], {"done_items": [1, 2]})
    core.close()                                                 # ...и процесс упал

    core2 = make_core()
    core2.recover(force=True)
    assert core2.get_task(a["id"])["status"] == "COMPLETED"      # результат A не потерян
    assert core2.attempts(a["id"])[0]["output_snapshot"] == json.dumps({"result": f"done {a['id']}"},
                                                                        separators=(",", ":"))
    seen = {}

    def ex(task, ctx):
        seen[task["id"]] = ctx.last_checkpoint
        return {"ok": task["id"]}

    clock.advance(100)
    core2.run_until_idle(ex)
    assert seen[b["id"]]["checkpoint_version"] == ctx_cp        # продолжила с checkpoint
    assert a["id"] not in seen                                   # A повторно не запускалась
    assert core2.get_task(parent["id"])["status"] == "COMPLETED"


def test_15b_external_side_effect_not_blindly_retried(make_core):
    core = make_core()
    t = core.create_task("отправить письмо", side_effects="external")
    core.claim_next()
    core.close()
    core2 = make_core()
    core2.recover(force=True)
    t2 = core2.get_task(t["id"])
    assert t2["status"] == "BLOCKED"
    assert "неизвестно" in t2["blocked_reason"] and t2["resume_condition"]
    assert core2.run_once(ok_executor) is None                  # сам не повторяется


def test_15c_reconciler_confirms_external_action(make_core):
    core = make_core()
    t = core.create_task("платёж (имитация)", side_effects="external")
    core.claim_next()
    core.close()
    core2 = make_core()

    def reconciler(task, attempt, checkpoint):
        return "done", {"receipt": "R-1"}                       # сверка: действие уже выполнено

    core2.recover(reconciler, force=True)
    assert core2.get_task(t["id"])["status"] == "VERIFYING"
    calls = []
    core2.run_once(lambda task, ctx: calls.append(1))
    assert calls == []                                            # повторно не выполнялось
    assert core2.get_task(t["id"])["status"] == "COMPLETED"


def test_15d_late_result_from_zombie_attempt(make_core):
    core = make_core()
    t = core.create_task("x")
    att = core.claim_next()
    core.recover(force=True)
    with pytest.raises(StaleAttemptError):
        core.finish_attempt(att["id"], success=True, output={"late": 1})
    assert len(core.events(t["id"], "late_result_ignored")) == 1
    assert core.get_task(t["id"])["status"] == "RETRY_WAIT"


# 16
def test_16_cyclic_dependencies_rejected(core):
    a = core.create_task("A")
    b = core.create_task("B", depends_on=[a["id"]])
    c = core.create_task("C", depends_on=[b["id"]])
    with pytest.raises(DependencyCycleError):
        core.add_dependency(a["id"], c["id"])                   # A -> C -> B -> A
    with pytest.raises(DependencyCycleError):
        core.add_dependency(a["id"], a["id"])
    assert core.conn.execute("SELECT COUNT(*) FROM task_dependencies").fetchone()[0] == 2


# 17
def test_17_cancel(core):
    t = core.create_task("x")
    assert core.cancel(t["id"])["status"] == "CANCELLED"
    assert core.cancel(t["id"])["status"] == "CANCELLED"       # повторная отмена безопасна
    with pytest.raises(InvalidTransitionError):
        core.transition(t["id"], "READY")
    done = core.create_task("y")
    core.run_once(ok_executor)
    with pytest.raises(InvalidTransitionError):
        core.cancel(done["id"])
    # Отмена выполняющейся задачи — после окончания попытки.
    r = core.create_task("running")
    att = core.claim_next()
    assert core.cancel(r["id"])["status"] == "RUNNING"
    assert core.finish_attempt(att["id"], success=True, output={"x": 1})["status"] == "CANCELLED"
    # Зависимая от отменённой задача не висит молча, а блокируется с причиной.
    p = core.create_task("p")
    d = core.create_task("d", depends_on=[p["id"]])
    core.cancel(p["id"])
    core.promote_ready()
    assert core.get_task(d["id"])["status"] == "BLOCKED"
    assert "отменена" in core.get_task(d["id"])["blocked_reason"]


# 18
def test_18_provider_unavailable(core, clock):
    core.register_provider("model-a", ["text"])
    t = core.create_task("нужна модель", required_capability="text")
    core.set_provider_status("model-a", unavailable_for=300)
    with pytest.raises(ProviderUnavailableError):
        core.select_provider("text")
    for _ in range(3):
        assert core.run_once(ok_executor) is None
    t2 = core.get_task(t["id"])
    assert t2["status"] == "READY" and t2["attempt_count"] == 0  # попытки не сожжены
    assert len(core.events(t["id"], "provider_unavailable")) == 1  # без спама в журнале
    clock.advance(301)
    assert core.run_once(ok_executor)["status"] == "COMPLETED"
    assert core.get_task(t["id"])["assigned_provider"] == "model-a"


def test_18b_provider_failure_during_attempt_is_retried(core, clock):
    core.register_provider("model-a", ["text"])
    t = core.create_task("x", required_capability="text")

    def ex(task, ctx):
        raise ProviderUnavailableError("503")

    assert core.run_once(ex)["status"] == "RETRY_WAIT"


def test_18c_no_secrets_in_provider_table(core):
    with pytest.raises(SecretDetectedError):
        core.register_provider("bad", ["text"], quota_state={"api_key": "x"})
    with pytest.raises(SecretDetectedError):
        core.register_provider("bad", ["text"], quota_state={"note": "sk-abcdefghijklmnopqrstuv"})


# 19
def test_19_blocked_needs_human(core):
    t = core.create_task("нужен человек")

    def ex(task, ctx):
        raise NeedsHumanError("нужно подтвердить доступ к аккаунту")

    assert core.run_once(ex)["status"] == "BLOCKED"
    t2 = core.get_task(t["id"])
    assert "подтвердить доступ" in t2["blocked_reason"] and t2["resume_condition"]
    assert core.run_once(ex) is None                             # сама не продолжается
    other = core.create_task("другая")
    with pytest.raises(ValidationError):
        core.block(other["id"], "")                              # без причины нельзя
    core.cancel(other["id"])
    assert core.resume(t["id"])["status"] == "READY"
    assert core.run_once(ok_executor)["status"] == "COMPLETED"


def test_19b_permanent_error_not_retried(core, clock):
    t = core.create_task("x", max_attempts=5)

    def ex(task, ctx):
        raise PermanentError("неверный формат задачи")

    assert core.run_once(ex)["status"] == "FAILED"
    assert core.get_task(t["id"])["attempt_count"] == 1


# 20
def test_20_no_unbounded_retry(core, clock):
    t = core.create_task("never works", max_attempts=4)
    calls = []

    def ex(task, ctx):
        calls.append(1)
        raise RuntimeError("неизвестная ошибка")

    for _ in range(50):
        core.run_once(ex)
        clock.advance(10_000)
    assert len(calls) == 4
    assert core.get_task(t["id"])["status"] == "FAILED"
    with pytest.raises(InvalidTransitionError):
        core.transition(t["id"], "PENDING")                      # только явный restart_failed
    # Лимит max_attempts ограничен и в схеме базы.
    with pytest.raises(ValidationError):
        core.create_task("y", max_attempts=1000)
    # Ручной перезапуск — явное действие с записью в журнал.
    assert core.restart_failed(t["id"])["status"] == "PENDING"
    assert len(core.events(t["id"], "manual_restart")) == 1
    # run_until_idle тоже ограничен по числу шагов.
    assert len(core.run_until_idle(ex, max_steps=3)) <= 3


# --- дополнительные ---

def test_verification_failure_is_not_completed(core, clock):
    t = core.create_task("x", max_attempts=2)
    assert core.run_once(lambda task, ctx: {}, )["status"] == "RETRY_WAIT"   # пустой результат
    clock.advance(1000)
    assert core.run_once(lambda task, ctx: None)["status"] == "FAILED"
    assert core.get_task(t["id"])["status"] != "COMPLETED"


def test_parent_not_completed_before_children(core):
    parent = core.create_task("parent")
    core.create_task("child", parent_task_id=parent["id"])
    step = core.run_once(ok_executor)
    assert step["task_id"] != parent["id"]                       # сначала подзадача
    assert core.run_once(ok_executor)["task_id"] == parent["id"]
    assert core.get_task(parent["id"])["status"] == "COMPLETED"


def test_concurrent_claim_single_winner(make_core):
    a = make_core()
    b = make_core()
    a.create_task("одна задача")
    got = [a.claim_next(), b.claim_next()]
    assert sum(x is not None for x in got) == 1


def test_handoff_packet(core):
    t = core.create_task("Цель задачи", "подробности")
    core.save_checkpoint(t["id"], {"completed_steps": ["шаг 1"], "pending_steps": ["шаг 2"],
                                   "exact_next_action": "сделать шаг 2",
                                   "notes": {"api_key": "sk-abcdefghijklmnopqrstuvwxyz"}})
    p = build_handoff(core, t["id"], source_commit="abc123")
    from taskcore.handoff import FIELDS
    assert set(FIELDS) <= set(p)
    assert p["completed_steps"] == ["шаг 1"] and p["exact_next_action"] == "сделать шаг 2"
    md = handoff_to_markdown(p)
    assert "sk-abc" not in md and "Цель задачи" in md


def test_schema_migration_is_idempotent(make_core):
    a = make_core()
    v = a.db.schema_version
    b = make_core()                                              # повторное открытие
    assert b.db.schema_version == v == 1
    fk = a.conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1


def test_demo_runs():
    from taskcore.__main__ import demo
    assert demo(quiet=True) is True

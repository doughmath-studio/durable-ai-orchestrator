import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from taskcore import TaskCore  # noqa: E402


class FakeClock:
    """Управляемое время: тесты не ждут реальных секунд."""

    def __init__(self, start=1_700_000_000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def make_core(db_path, clock):
    cores = []

    def _make(**kw):
        kw.setdefault("clock", clock)
        kw.setdefault("rng", lambda: 1.0)  # jitter детерминирован в тестах
        kw.setdefault("backoff_base", 10.0)
        c = TaskCore(db_path, **kw)
        cores.append(c)
        return c

    yield _make
    for c in cores:
        try:
            c.close()
        except Exception:
            pass


@pytest.fixture
def core(make_core):
    return make_core()


def ok_executor(task, ctx):
    return {"result": f"done {task['id']}"}


def accept_all(task, output):
    return True, "ok"

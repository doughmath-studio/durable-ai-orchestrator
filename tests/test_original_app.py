"""Исходное приложение (FastAPI + панель + Groq/HuggingFace) после реанимации."""
import asyncio
import os
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db import Database  # noqa: E402
from models import TaskStatus  # noqa: E402
from task_manager import TaskManager  # noqa: E402


def test_no_invisible_characters_in_file_names():
    names = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                           text=True).stdout.split("\0")
    bad = [n for n in names if n and not n.isascii()]
    assert bad == []


@pytest.fixture
def tm(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    db.init_db(os.path.join(ROOT, "schema.sql"))
    return TaskManager(db)


def test_interrupted_task_returns_to_queue(tm):
    task = tm.create_task("Задача")
    tm.update_status(task.id, TaskStatus.RUNNING)
    assert tm.recover_interrupted() == 1
    t = tm.get_task(task.id)
    assert t.status == TaskStatus.RETRY_WAIT and t.attempt_count == 1


def test_worker_completes_task_with_fake_provider(tm, monkeypatch):
    import worker as worker_mod

    class FakeRouter:
        def __init__(self, providers):
            pass

        def execute_with_fallback(self, prompt, ctx):
            assert "Задача" in prompt and "подробности" in prompt
            return "готово", "Fake"

    monkeypatch.setattr(worker_mod, "ModelRouter", FakeRouter)
    task = tm.create_task("Задача", "подробности")
    w = worker_mod.BackgroundWorker(tm, poll_interval=0.01)

    async def run():
        w.start()
        for _ in range(200):
            await asyncio.sleep(0.01)
            if tm.get_task(task.id).status == TaskStatus.COMPLETED:
                break
        await w.stop()

    asyncio.run(run())
    t = tm.get_task(task.id)
    assert t.status == TaskStatus.COMPLETED and "готово" in t.result_summary


def test_web_api_and_dashboard(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    shutil.copy(os.path.join(ROOT, "schema.sql"), tmp_path)
    shutil.copytree(os.path.join(ROOT, "static"), tmp_path / "static")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("HF_API_KEY", raising=False)
    sys.modules.pop("main", None)
    import main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        assert "AI Orchestrator" in c.get("/").text
        r = c.post("/api/tasks", json={"title": "Проверка", "description": ""})
        assert r.status_code == 200 and r.json()["task"]["status"] == "READY"
        titles = [t["title"] for t in c.get("/api/tasks").json()["tasks"]]
        assert titles == ["Проверка"]
        assert c.get("/api/worker/status").json()["is_running"] is True
        assert c.get("/api/backup/export").status_code == 200


def test_dashboard_shows_answer_and_error():
    html = open(os.path.join(ROOT, "static", "index.html"), encoding="utf-8").read()
    assert "result_summary" in html and "error_summary" in html


def test_groq_uses_a_model_that_is_still_available(monkeypatch):
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    from providers.groq_adapter import GroqProviderAdapter
    # llama-3.1-8b-instant отключён Groq 16.08.2026
    assert GroqProviderAdapter(api_key="x").model == "openai/gpt-oss-20b"
    monkeypatch.setenv("GROQ_MODEL", "other")
    assert GroqProviderAdapter(api_key="x").model == "other"

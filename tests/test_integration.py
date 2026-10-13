import pytest
from db import Database
from task_manager import TaskManager
from models import TaskStatus

def test_full_flow(tmp_path):
    db_file = str(tmp_path / "test.db")
    db = Database(db_file)
    db.init_db("schema.sql")
    tm = TaskManager(db)

    task = tm.create_task("Test Integration")
    assert task.status == TaskStatus.READY

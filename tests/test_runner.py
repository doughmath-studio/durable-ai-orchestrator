"""Этап B: такт системы (Issues -> задачи -> нейросеть -> ответы) без интернета."""
import json

import pytest

from taskcore import TaskCore
from taskcore.github import GitHubClient, ModelsClient
from taskcore.runner import tick

OWNER = "owner"


class FakeGitHub:
    """Имитация API GitHub и GitHub Models в памяти."""

    def __init__(self):
        self.issues = {}
        self.comments = {}
        self.model_replies = []  # очередь: (код, текст ответа)
        self.prompts = []
        self.fail_comment_post_once = False

    def add_issue(self, number, title, body="", author=OWNER, pr=False):
        issue = {"number": number, "title": title, "body": body, "state": "open",
                 "user": {"login": author}}
        if pr:
            issue["pull_request"] = {}
        self.issues[number] = issue
        self.comments.setdefault(number, [])

    def __call__(self, method, url, headers, body, timeout):
        assert headers["Authorization"] == "Bearer t"
        payload = json.loads(body) if body else None
        if url.startswith("https://models.github.ai/"):
            self.prompts.append(payload["messages"][-1]["content"])
            code, text = self.model_replies.pop(0) if self.model_replies else (200, "ответ")
            if code != 200:
                return code, b"error"
            return 200, json.dumps({"choices": [{"message": {"content": text}}]}).encode()
        path = url.split("/repos/o/r", 1)[1]
        if method == "GET" and path.startswith("/issues?"):
            return 200, json.dumps([i for i in self.issues.values() if i["state"] == "open"]).encode()
        num = int(path.split("/")[2].split("?")[0])
        if method == "GET" and "/comments" in path:
            return 200, json.dumps(self.comments[num]).encode()
        if method == "POST" and path.endswith("/comments"):
            self.comments[num].append({"body": payload["body"]})
            if self.fail_comment_post_once:  # комментарий ушёл, а ответ потерялся
                self.fail_comment_post_once = False
                raise ConnectionError("обрыв после отправки")
            return 201, b"{}"
        if method == "PATCH":
            self.issues[num]["state"] = payload["state"]
            return 200, b"{}"
        raise AssertionError(f"неожиданный запрос {method} {url}")


@pytest.fixture
def fake():
    return FakeGitHub()


def run(db_path, fake, **kw):
    core = TaskCore(db_path, backoff_base=0.0)
    try:
        return tick(core, GitHubClient("t", "o/r", transport=fake),
                    ModelsClient("t", transport=fake).ask, [OWNER], **kw)
    finally:
        core.close()


def test_issue_gets_answer_and_is_closed(db_path, fake):
    fake.add_issue(1, "Сколько будет 2+2?", "Ответь числом")
    fake.model_replies.append((200, "4"))
    report = run(db_path, fake)
    assert report["stats"].get("COMPLETED") == 1
    assert fake.issues[1]["state"] == "closed"
    assert len(fake.comments[1]) == 1 and fake.comments[1][0]["body"].startswith("4")
    assert "Сколько будет 2+2?" in fake.prompts[0] and "Ответь числом" in fake.prompts[0]


def test_second_tick_does_nothing_new(db_path, fake):
    fake.add_issue(1, "Задача")
    run(db_path, fake)
    fake.issues[1]["state"] = "open"  # даже если Issue снова открыли вручную
    report = run(db_path, fake)
    assert report["new_tasks"] == [] and report["steps"] == [] and report["reported"] == []
    assert len(fake.comments[1]) == 1 and len(fake.prompts) == 1


def test_strangers_and_pull_requests_are_ignored(db_path, fake):
    fake.add_issue(1, "чужая", author="stranger")
    fake.add_issue(2, "PR", pr=True)
    report = run(db_path, fake)
    assert report["new_tasks"] == [] and fake.prompts == []
    assert fake.comments[1] == [] and fake.comments[2] == []


def test_rate_limit_retries_on_next_tick(db_path, fake):
    fake.add_issue(1, "Задача")
    fake.model_replies.append((429, ""))
    report = run(db_path, fake, max_steps=1)
    assert report["stats"].get("RETRY_WAIT") == 1 and fake.comments[1] == []
    fake.model_replies.append((200, "готово"))
    report = run(db_path, fake)
    assert report["stats"].get("COMPLETED") == 1
    assert fake.comments[1][0]["body"].startswith("готово")


def test_no_access_blocks_task_and_tells_owner(db_path, fake):
    fake.add_issue(1, "Задача")
    fake.model_replies.append((403, ""))
    report = run(db_path, fake)
    assert report["stats"].get("BLOCKED") == 1
    assert fake.issues[1]["state"] == "open"
    assert "ждёт человека" in fake.comments[1][0]["body"]


def test_permanent_errors_fail_with_one_comment(db_path, fake):
    fake.add_issue(1, "Задача")
    fake.model_replies.append((400, ""))
    report = run(db_path, fake)
    assert report["stats"].get("FAILED") == 1
    assert len(fake.comments[1]) == 1 and "Не получилось" in fake.comments[1][0]["body"]
    run(db_path, fake)
    assert len(fake.comments[1]) == 1


def test_crash_after_comment_does_not_duplicate_it(db_path, fake):
    fake.add_issue(1, "Задача")
    fake.fail_comment_post_once = True
    with pytest.raises(ConnectionError):
        run(db_path, fake)
    assert len(fake.comments[1]) == 1 and fake.issues[1]["state"] == "open"
    run(db_path, fake)  # сверка находит уже отправленный комментарий
    assert len(fake.comments[1]) == 1 and fake.issues[1]["state"] == "closed"
    assert len(fake.prompts) == 1


def test_crash_during_model_call_is_recovered(db_path, fake):
    fake.add_issue(1, "Задача")
    core = TaskCore(db_path)
    core.create_task("Задача", "", idempotency_key="gh-issue-1", max_attempts=3)
    core.promote_ready()
    core.claim_next()  # попытка началась, и процесс «упал»
    core.close()
    report = run(db_path, fake)
    assert report["recovered"][0]["result"] == "RETRY_WAIT"
    report = run(db_path, fake)
    assert report["stats"].get("COMPLETED") == 1 and len(fake.comments[1]) == 1

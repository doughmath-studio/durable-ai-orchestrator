"""Демо-канал в Telegram: пост от нейросети уходит в канал, без интернета."""
import json

import pytest

from taskcore.channel import FOOTER, load_history, post_once
from taskcore.errors import PermanentError

ENV = {"TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHANNEL": "@demo",
       "GROQ_API_KEY": "gk", "CHANNEL_TOPIC": "Маркетинг для малого бизнеса"}


class Fake:
    def __init__(self, answer="Как найти первых клиентов\n\nАбзац один.\n\nАбзац два.",
                 telegram_ok=True):
        self.answer = answer
        self.telegram_ok = telegram_ok
        self.prompts = []
        self.sent = []

    def __call__(self, method, url, headers, body, timeout):
        payload = json.loads(body)
        if url.endswith("/chat/completions"):
            self.prompts.append(payload["messages"][-1]["content"])
            return 200, json.dumps({"choices": [{"message": {"content": self.answer}}]}).encode()
        assert url == "https://api.telegram.org/bot123:abc/sendMessage"
        if not self.telegram_ok:
            return 400, json.dumps({"ok": False, "description": "chat not found"}).encode()
        self.sent.append(payload)
        return 200, json.dumps({"ok": True, "result": {"message_id": len(self.sent)}}).encode()


@pytest.fixture
def env(tmp_path):
    return dict(ENV, CHANNEL_HISTORY=str(tmp_path / "history.json"))


def test_post_goes_to_channel_with_footer(env):
    fake = Fake()
    report = post_once(env, transport=fake)
    assert report == {"posted": True, "title": "Как найти первых клиентов", "message_id": 1}
    assert fake.sent[0]["chat_id"] == "@demo"
    assert fake.sent[0]["text"].startswith("Как найти первых клиентов\n\nАбзац один.")
    assert fake.sent[0]["text"].endswith(FOOTER)
    assert "Маркетинг для малого бизнеса" in fake.prompts[0]


def test_previous_titles_are_passed_so_topics_do_not_repeat(env):
    fake = Fake()
    post_once(env, transport=fake)
    fake.answer = "Вторая тема\n\nТекст."
    post_once(env, transport=fake)
    assert "Как найти первых клиентов" in fake.prompts[1]
    assert [h["title"] for h in load_history(env["CHANNEL_HISTORY"])] == [
        "Как найти первых клиентов", "Вторая тема"]


def test_nothing_is_sent_until_channel_is_configured(tmp_path):
    fake = Fake()
    report = post_once({"CHANNEL_HISTORY": str(tmp_path / "h.json")}, transport=fake)
    assert report["posted"] is False and fake.prompts == [] and fake.sent == []
    assert "TELEGRAM_BOT_TOKEN" in report["missing"] and "CHANNEL_TOPIC" in report["missing"]


def test_telegram_error_is_reported_and_history_unchanged(env):
    with pytest.raises(PermanentError) as err:
        post_once(env, transport=Fake(telegram_ok=False))
    assert "chat not found" in str(err.value) and "123:abc" not in str(err.value)
    assert load_history(env["CHANNEL_HISTORY"]) == []


def test_markdown_title_is_cleaned(env):
    fake = Fake(answer="**«Пять ошибок в рекламе»**\n\nТекст.")
    report = post_once(env, transport=fake)
    assert report["title"] == "Пять ошибок в рекламе"

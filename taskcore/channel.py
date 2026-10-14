"""Демо-канал в Telegram: ИИ сам пишет пост по теме канала и публикует его.

Запускается по расписанию GitHub Actions (бесплатно, без «засыпания»).
История последних постов хранится в JSON, чтобы темы не повторялись.
Только стандартная библиотека; сеть вынесена в transport для тестов.
"""
import json
import os
import sys
from datetime import datetime, timezone

from .errors import NeedsHumanError, PermanentError, TransientError
from .github import Router, _check, clients_from_env, urllib_transport

TELEGRAM_API = "https://api.telegram.org"
HISTORY_LIMIT = 30
FOOTER = "🤖 Канал ведётся 100% автономным ИИ."
MAX_POST_CHARS = 3500  # у Telegram предел 4096 символов на сообщение


def build_prompt(topic, history):
    recent = "\n".join(f"- {h}" for h in history[-HISTORY_LIMIT:]) or "- (постов ещё не было)"
    return (
        f"Ты ведёшь Telegram-канал на тему: «{topic}».\n"
        "Напиши один новый пост на русском языке.\n"
        "Требования:\n"
        "- первая строка: короткий заголовок без кавычек и без слова «Заголовок»;\n"
        "- затем 3–6 коротких абзацев полезного содержания (советы, разбор, объяснение);\n"
        "- не выдумывай свежие новости, даты, цены и цитаты: у тебя нет доступа к интернету;\n"
        "- без хештегов и без markdown-разметки.\n"
        f"Уже были посты с такими заголовками, не повторяйся:\n{recent}"
    )


def clean_post(text):
    lines = [line.strip().strip("*#").strip() for line in (text or "").strip().splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    if not lines:
        raise TransientError("нейросеть вернула пустой пост")
    title = lines[0].strip("«»\"' ")
    body = "\n".join(lines[1:]).strip()
    post = f"{title}\n\n{body}".strip()[:MAX_POST_CHARS]
    return title, f"{post}\n\n{FOOTER}"


class TelegramClient:
    def __init__(self, token, chat_id, *, transport=urllib_transport, api_url=TELEGRAM_API,
                 timeout=30):
        self.token = token
        self.chat_id = chat_id
        self.transport = transport
        self.api_url = api_url
        self.timeout = timeout

    def send_message(self, text):
        url = f"{self.api_url}/bot{self.token}/sendMessage"
        body = json.dumps({"chat_id": self.chat_id, "text": text,
                           "disable_web_page_preview": True}).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "ai-orchestrator/1.0"}
        status, data = self.transport("POST", url, headers, body, self.timeout)
        # Токен бота входит в адрес: в тексте ошибки адреса нет, только ответ Telegram.
        _check(status, data, "Telegram")
        reply = json.loads(data)
        if not reply.get("ok"):
            raise PermanentError(f"Telegram: {reply.get('description')}")
        return reply["result"].get("message_id")


def load_history(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_history(path, history):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history[-HISTORY_LIMIT:], f, ensure_ascii=False, indent=1)


def post_once(env, *, transport=urllib_transport, now=None):
    """Один пост. Возвращает отчёт; при нехватке настроек ничего не отправляет."""
    missing = [k for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL") if not env.get(k)]
    clients = clients_from_env(env, transport=transport)
    if not clients:
        missing.append("GROQ_API_KEY или HF_API_KEY")
    topic = (env.get("CHANNEL_TOPIC") or "").strip()
    if not topic:
        missing.append("CHANNEL_TOPIC")
    if missing:
        return {"posted": False, "missing": missing}

    history_path = env.get("CHANNEL_HISTORY", "data/channel_history.json")
    history = load_history(history_path)
    titles = [h["title"] for h in history if isinstance(h, dict) and h.get("title")]
    title, text = clean_post(Router(clients).ask(build_prompt(topic, titles)))
    message_id = TelegramClient(env["TELEGRAM_BOT_TOKEN"].strip(), env["TELEGRAM_CHANNEL"].strip(),
                                transport=transport).send_message(text)
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    history.append({"title": title, "message_id": message_id, "posted_at": stamp})
    save_history(history_path, history)
    return {"posted": True, "title": title, "message_id": message_id}


def main():
    try:
        report = post_once(dict(os.environ))
    except (TransientError, NeedsHumanError, PermanentError) as e:
        print(f"пост не опубликован: {e}")
        return 1
    if not report["posted"]:
        print("канал ещё не настроен, не хватает: " + ", ".join(report["missing"]))
        return 0
    print(f"опубликован пост: {report['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

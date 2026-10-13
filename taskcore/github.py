"""Связь с GitHub Issues (входящие задачи и ответы) и с бесплатными нейросетями (Groq, HuggingFace).

Только стандартная библиотека. Сетевой вызов вынесен в transport, чтобы тесты
работали без интернета.
"""
import json
import urllib.error
import urllib.request

from .errors import NeedsHumanError, PermanentError, TransientError

API_URL = "https://api.github.com"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
HF_URL = "https://router.huggingface.co/v1/chat/completions"
HF_MODEL = "meta-llama/Llama-3.1-8B-Instruct"
SYSTEM_PROMPT = ("Ты помощник в автономной системе задач. Отвечай на языке задачи, "
                 "понятно и по делу. Если задача неясна, скажи, чего не хватает.")


def urllib_transport(method, url, headers, body, timeout):
    """Настоящий HTTP-запрос. Возвращает (код ответа, тело в байтах)."""
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.geturl() != url:
                # urllib превращает POST при переадресации в GET без тела — это ошибка адреса.
                raise PermanentError(f"запрос переадресован на {resp.geturl()}")
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TransientError(f"сеть недоступна: {e}") from e


def _check(status, data, what):
    """Переводит HTTP-код в тип ошибки ядра."""
    if 200 <= status < 300:
        return
    text = data.decode("utf-8", "replace")[:300] if data else ""
    if status == 429 or status >= 500:
        raise TransientError(f"{what}: временная ошибка {status}: {text}")
    if status in (401, 403):
        raise NeedsHumanError(f"{what}: нет доступа ({status}): {text}")
    raise PermanentError(f"{what}: ошибка {status}: {text}")


class GitHubClient:
    def __init__(self, token, repo, *, transport=urllib_transport, api_url=API_URL,
                 timeout=30):
        self.token = token
        self.repo = repo
        self.transport = transport
        self.api_url = api_url
        self.timeout = timeout

    def _call(self, method, path, payload=None):
        headers = {"Accept": "application/vnd.github+json",
                   "Authorization": f"Bearer {self.token}",
                   "X-GitHub-Api-Version": "2022-11-28",
                   "User-Agent": "taskcore"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        status, data = self.transport(method, self.api_url + path, headers, body, self.timeout)
        _check(status, data, f"GitHub {method} {path}")
        return json.loads(data) if data else None

    def open_issues(self):
        """Открытые Issues (без pull request'ов), старые первыми."""
        items = self._call("GET", f"/repos/{self.repo}/issues?state=open&per_page=100"
                                  "&sort=created&direction=asc") or []
        return [i for i in items if "pull_request" not in i]

    def comments(self, number):
        return self._call("GET", f"/repos/{self.repo}/issues/{number}/comments?per_page=100") or []

    def add_comment(self, number, body):
        return self._call("POST", f"/repos/{self.repo}/issues/{number}/comments", {"body": body})

    def close_issue(self, number):
        return self._call("PATCH", f"/repos/{self.repo}/issues/{number}",
                          {"state": "closed", "state_reason": "completed"})


class ChatClient:
    """Нейросеть с OpenAI-совместимым адресом (Groq, HuggingFace). Ключ — из секретов GitHub."""

    def __init__(self, name, url, model, api_key, *, transport=urllib_transport, timeout=120):
        self.name = name
        self.url = url
        self.model = model
        self.api_key = api_key
        self.transport = transport
        self.timeout = timeout

    def ask(self, prompt):
        payload = {"model": self.model,
                   "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": prompt}]}
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json",
                   "Accept": "application/json",
                   "User-Agent": "taskcore"}
        status, data = self.transport("POST", self.url, headers,
                                      json.dumps(payload).encode("utf-8"), self.timeout)
        _check(status, data, self.name)
        try:
            answer = json.loads(data)["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            snippet = data.decode("utf-8", "replace")[:300] if data else "(пусто)"
            raise TransientError(f"{self.name}: непонятный ответ (код {status}): {snippet}") from e
        return (answer or "").strip()


class Router:
    """Пробует нейросети по очереди: если первая недоступна, отвечает следующая."""

    def __init__(self, clients):
        self.clients = list(clients)

    def ask(self, prompt):
        errors = []
        for client in self.clients:
            try:
                answer = client.ask(prompt)
            except (TransientError, NeedsHumanError, PermanentError) as e:
                errors.append(e)
                continue
            if answer:
                return answer
            errors.append(TransientError(f"{client.name}: пустой ответ"))
        text = "; ".join(str(e) for e in errors) or "нет ни одной нейросети с ключом"
        # Если хоть одна ошибка временная — повторим позже.
        if not errors or any(isinstance(e, TransientError) for e in errors):
            raise TransientError(text)
        if any(isinstance(e, NeedsHumanError) for e in errors):
            raise NeedsHumanError(text)
        raise PermanentError(text)


def clients_from_env(env, transport=urllib_transport):
    """Нейросети, для которых в секретах есть ключ. Порядок: Groq, затем HuggingFace."""
    clients = []
    groq_key = env.get("GROQ_API_KEY") or env.get("LLM_API_KEY")
    if groq_key:
        clients.append(ChatClient("Groq", GROQ_URL, env.get("GROQ_MODEL") or GROQ_MODEL,
                                  groq_key.strip(), transport=transport))
    if env.get("HF_API_KEY"):
        clients.append(ChatClient("HuggingFace", HF_URL, env.get("HF_MODEL") or HF_MODEL,
                                  env["HF_API_KEY"].strip(), transport=transport))
    return clients

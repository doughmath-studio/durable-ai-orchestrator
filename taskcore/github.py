"""Связь с GitHub: Issues (входящие задачи и ответы) и GitHub Models (бесплатная нейросеть).

Только стандартная библиотека. Сетевой вызов вынесен в transport, чтобы тесты
работали без интернета.
"""
import json
import urllib.error
import urllib.request

from .errors import NeedsHumanError, PermanentError, TransientError

API_URL = "https://api.github.com"
MODELS_URL = "https://models.github.ai/inference/chat/completions"
DEFAULT_MODEL = "openai/gpt-4o-mini"
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


class ModelsClient:
    """GitHub Models: работает с обычным GITHUB_TOKEN из Actions (permissions: models: read)."""

    def __init__(self, token, model=DEFAULT_MODEL, *, transport=urllib_transport,
                 url=MODELS_URL, timeout=120):
        self.token = token
        self.model = model
        self.transport = transport
        self.url = url
        self.timeout = timeout

    def ask(self, prompt):
        payload = {"model": self.model,
                   "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": prompt}]}
        headers = {"Authorization": f"Bearer {self.token}",
                   "Content-Type": "application/json",
                   "Accept": "application/json",
                   "User-Agent": "taskcore"}
        status, data = self.transport("POST", self.url, headers,
                                      json.dumps(payload).encode("utf-8"), self.timeout)
        _check(status, data, "GitHub Models")
        try:
            answer = json.loads(data)["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as e:
            snippet = data.decode("utf-8", "replace")[:300] if data else "(пусто)"
            raise TransientError(f"GitHub Models: непонятный ответ (код {status}): {snippet}") from e
        return (answer or "").strip()

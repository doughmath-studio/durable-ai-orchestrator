import os
import json
import urllib.request
import urllib.error
from typing import Dict, Any
from providers.base import BaseProviderAdapter
from budget_guard import BudgetGuard

class GroqProviderAdapter(BaseProviderAdapter):
    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY", "")
        # llama-3.1-8b-instant Groq отключил 16.08.2026; замена по их списку — openai/gpt-oss-20b
        self.model = model or os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
        self.endpoint = "https://api.groq.com/openai/v1/chat/completions"
        self.budget_guard = BudgetGuard(max_rpm=14, max_rpd=1000)

    @property
    def name(self) -> str:
        return f"Groq ({self.model})"

    def is_available(self) -> bool:
        return bool(self.api_key and len(self.api_key.strip()) > 0)

    def execute_task(self, prompt: str, task_context: Dict[str, Any]) -> str:
        if not self.is_available():
            raise ValueError("API-ключ Groq не настроен.")

        self.budget_guard.check_and_claim()
        headers = {"Authorization": f"Bearer {self.api_key.strip()}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are an autonomous AI agent working inside an execution queue."},
                {"role": "user", "content": f"Task: {prompt}\nContext: {json.dumps(task_context, ensure_ascii=False)}"}
            ]
        }
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(self.endpoint, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.loads(response.read().decode('utf-8'))
                return result["choices"][0]["message"]["content"]
        except Exception as e:
            raise RuntimeError(f"Groq API Error: {str(e)}")

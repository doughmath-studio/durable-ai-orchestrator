import os
import json
import urllib.request
from typing import Dict, Any
from providers.base import BaseProviderAdapter
from budget_guard import BudgetGuard

class HuggingFaceAdapter(BaseProviderAdapter):
    def __init__(self, api_key: str = None, model: str = "Qwen/Qwen2.5-72B-Instruct"):
        self.api_key = api_key or os.getenv("HF_API_KEY", "")
        self.model = model
        self.endpoint = "https://router.huggingface.co/v1/chat/completions"
        self.budget_guard = BudgetGuard(max_rpm=10, max_rpd=500)

    @property
    def name(self) -> str:
        return f"HuggingFace ({self.model.split('/')[-1]})"

    def is_available(self) -> bool:
        return bool(self.api_key and len(self.api_key.strip()) > 0)

    def execute_task(self, prompt: str, task_context: Dict[str, Any]) -> str:
        if not self.is_available():
            raise ValueError("HF API-ключ не настроен.")

        self.budget_guard.check_and_claim()
        headers = {"Authorization": f"Bearer {self.api_key.strip()}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are a backup AI agent worker."},
                {"role": "user", "content": f"Task: {prompt}\nContext: {json.dumps(task_context, ensure_ascii=False)}"}
            ]
        }
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(self.endpoint, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=35) as response:
                result = json.loads(response.read().decode('utf-8'))
                return result["choices"][0]["message"]["content"]
        except Exception as e:
            raise RuntimeError(f"HF API Error: {str(e)}")

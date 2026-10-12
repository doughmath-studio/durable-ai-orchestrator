from typing import List, Tuple, Dict, Any
from providers.base import BaseProviderAdapter

class ModelRouter:
    def __init__(self, providers: List[BaseProviderAdapter]):
        self.providers = providers

    def execute_with_fallback(self, prompt: str, task_context: Dict[str, Any]) -> Tuple[str, str]:
        errors = []
        available = [p for p in self.providers if p.is_available()]

        if not available:
            raise RuntimeError("Нет доступных ИИ-провайдеров. Настройте API-ключи.")

        for provider in available:
            try:
                result = provider.execute_task(prompt, task_context)
                return result, provider.name
            except Exception as e:
                errors.append(f"[{provider.name}]: {str(e)}")

        raise RuntimeError("Все провайдеры недоступны:\n" + "\n".join(errors))

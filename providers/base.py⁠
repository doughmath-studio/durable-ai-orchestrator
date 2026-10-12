from abc import ABC, abstractmethod
from typing import Dict, Any

class BaseProviderAdapter(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def is_available(self) -> bool:
        pass

    @abstractmethod
    def execute_task(self, prompt: str, task_context: Dict[str, Any]) -> str:
        pass

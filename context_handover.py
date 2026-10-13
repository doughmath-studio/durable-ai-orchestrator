import json
import re
from dataclasses import dataclass, asdict, field
from typing import List, Dict

@dataclass
class HandoverPackage:
    project_id: str
    task_id: str
    task_goal: str
    acceptance_criteria: List[str] = field(default_factory=list)
    exact_next_action: str = ""

class ContextHandoverManager:
    SECRET_PATTERNS = [r"gsk_[A-Za-z0-9_-]{20,}", r"hf_[A-Za-z0-9_-]{20,}", r"sk-[A-Za-z0-9_-]{20,}"]

    @classmethod
    def sanitize_text(cls, text: str) -> str:
        sanitized = text
        for pattern in cls.SECRET_PATTERNS:
            sanitized = re.sub(pattern, "[REDACTED_SECRET]", sanitized, flags=re.IGNORECASE)
        return sanitized

    @classmethod
    def create_package(cls, task_id: str, title: str, description: str, exact_next_action: str = "") -> HandoverPackage:
        return HandoverPackage(
            project_id="autonomous_multi_llm_system",
            task_id=task_id,
            task_goal=f"{title}\n\n{description}".strip(),
            acceptance_criteria=["Выполнить без ошибок"],
            exact_next_action=exact_next_action or f"Выполнить {title}"
        )

    @classmethod
    def to_prompt(cls, package: HandoverPackage) -> str:
        raw_json = json.dumps(asdict(package), ensure_ascii=False, indent=2)
        return f"=== AI TASK HANDOVER ===\n{cls.sanitize_text(raw_json)}\n========================"

import random
import re
from enum import Enum
from datetime import datetime, timedelta, timezone

class ErrorCategory(str, Enum):
    TRANSIENT = "TRANSIENT"
    FATAL = "FATAL"

class ErrorClassifier:
    PATTERNS_FATAL = [r"401", r"403", r"invalid_api_key", r"authentication", r"context_length_exceeded"]
    PATTERNS_TRANSIENT = [r"429", r"500", r"502", r"503", r"timeout", r"rate_limit"]

    @classmethod
    def classify(cls, error: Exception) -> ErrorCategory:
        err_str = str(error).lower()
        for pattern in cls.PATTERNS_FATAL:
            if re.search(pattern, err_str):
                return ErrorCategory.FATAL
        return ErrorCategory.TRANSIENT

class RetryManager:
    def __init__(self, base_delay: int = 5, max_delay: int = 300):
        self.base_delay = base_delay
        self.max_delay = max_delay

    def calculate_next_available_time(self, attempt_count: int) -> str:
        exponent = min(attempt_count, 10)
        backoff = self.base_delay * (2 ** exponent)
        jitter = random.uniform(0.5, 2.5)
        total_delay = min(self.max_delay, backoff + jitter)
        return (datetime.now(timezone.utc) + timedelta(seconds=total_delay)).isoformat()

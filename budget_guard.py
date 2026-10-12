import time
from typing import Dict, Any

class QuotaExceededError(Exception):
    pass

class BudgetGuard:
    def __init__(self, max_rpm: int = 14, max_rpd: int = 1000):
        self.max_rpm = max_rpm
        self.max_rpd = max_rpd
        self.request_timestamps = []
        self.daily_count = 0
        self.last_day_reset = time.time()

    def _refresh(self):
        now = time.time()
        self.request_timestamps = [ts for ts in self.request_timestamps if now - ts < 60]
        if now - self.last_day_reset >= 86400:
            self.daily_count = 0
            self.last_day_reset = now

    def check_and_claim(self):
        self._refresh()
        if len(self.request_timestamps) >= self.max_rpm:
            raise QuotaExceededError(f"Достигнут лимит RPM ({self.max_rpm} запр/мин)")
        if self.daily_count >= self.max_rpd:
            raise QuotaExceededError(f"Достигнут суточный лимит RPD ({self.max_rpd} запр/день)")
        
        self.request_timestamps.append(time.time())
        self.daily_count += 1

    def get_metrics(self) -> Dict[str, Any]:
        self._refresh()
        return {
            "current_rpm": len(self.request_timestamps),
            "max_rpm": self.max_rpm,
            "daily_count": self.daily_count,
            "max_rpd": self.max_rpd
        }

import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parent
_root_s = str(_repo_root)
if _root_s not in sys.path:
    sys.path.insert(0, _root_s)

import asyncio
import os
import logging
from task_manager import TaskManager
from model_router import ModelRouter
from providers.groq_adapter import GroqProviderAdapter
from providers.huggingface_adapter import HuggingFaceAdapter
from context_handover import ContextHandoverManager
from models import TaskStatus

logger = logging.getLogger("Worker")

class BackgroundWorker:
    def __init__(self, task_manager: TaskManager, poll_interval: float = 3.0):
        self.tm = task_manager
        self.poll_interval = poll_interval
        self.is_running = False
        self._task = None

    def start(self):
        if not self.is_running:
            self.is_running = True
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        if self.is_running:
            self.is_running = False
            if self._task:
                self._task.cancel()

    async def _run_loop(self):
        while self.is_running:
            task = None
            try:
                task = self.tm.get_next_ready_task()
                if task:
                    groq_key = open(".groq_key").read().strip() if os.path.exists(".groq_key") else ""
                    hf_key = open(".hf_key").read().strip() if os.path.exists(".hf_key") else ""
                    router = ModelRouter([GroqProviderAdapter(api_key=groq_key), HuggingFaceAdapter(api_key=hf_key)])
                    
                    self.tm.update_status(task.id, TaskStatus.RUNNING)
                    pkg = ContextHandoverManager.create_package(task.id, task.title, task.description)
                    
                    result, provider_name = await asyncio.to_thread(router.execute_with_fallback, ContextHandoverManager.to_prompt(pkg), {})
                    self.tm.update_status(task.id, TaskStatus.COMPLETED, result_summary=f"[{provider_name}]: {result}")
                else:
                    await asyncio.sleep(self.poll_interval)
            except Exception as e:
                if task:
                    self.tm.record_attempt_failure(task.id, e)
                await asyncio.sleep(self.poll_interval)

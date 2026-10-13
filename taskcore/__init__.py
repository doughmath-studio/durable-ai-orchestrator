"""Durable Task Core — этап A автономной multi-LLM системы."""
from .core import TaskCore, TRANSITIONS, STATUSES, TERMINAL, basic_verifier, is_allowed
from .errors import *  # noqa: F401,F403
from .handoff import build_handoff, handoff_to_markdown

__version__ = "0.1.0"

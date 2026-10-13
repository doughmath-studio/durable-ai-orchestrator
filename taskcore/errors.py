"""Типы ошибок ядра и ошибок исполнителей."""


class TaskCoreError(Exception):
    """Базовая ошибка ядра."""


class NotFoundError(TaskCoreError):
    pass


class ValidationError(TaskCoreError):
    pass


class InvalidTransitionError(TaskCoreError):
    pass


class DependencyCycleError(TaskCoreError):
    pass


class IdempotencyConflictError(TaskCoreError):
    """Тот же idempotency_key, но другие параметры запроса."""


class AttemptAlreadyFinishedError(TaskCoreError):
    pass


class StaleAttemptError(TaskCoreError):
    """Результат пришёл от попытки, которую уже закрыло восстановление."""


class CheckpointCorruptError(TaskCoreError):
    pass


class SecretDetectedError(ValidationError):
    """В данные провайдера попытались записать что-то похожее на секрет."""


# --- Ошибки, которые может бросить исполнитель задачи (executor) ---

class ExecutorError(Exception):
    kind = "transient"


class TransientError(ExecutorError):
    """Временная ошибка: можно повторить позже (сеть, лимит, таймаут)."""
    kind = "transient"


class PermanentError(ExecutorError):
    """Постоянная ошибка: повтор не поможет."""
    kind = "permanent"


class NeedsHumanError(ExecutorError):
    """Нужен человек: задача уходит в BLOCKED с причиной."""
    kind = "needs_human"


class ProviderUnavailableError(TransientError):
    """Провайдер (модель/инструмент) недоступен."""
    kind = "transient"

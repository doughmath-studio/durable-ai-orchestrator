import sys
from pathlib import Path

# Автоматически добавляем корень репозитория в начало sys.path,
# чтобы все модули внутри пакета providers могли импортировать budget_guard и др.
_repo_root = Path(__file__).resolve().parent.parent
_root_s = str(_repo_root)

if _root_s not in sys.path:
    sys.path.insert(0, _root_s)

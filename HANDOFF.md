# HANDOFF — future / этап A (Durable Task Core)

- **project_id**: future
- **task_id**: stage-A
- **task_goal**: устойчивое ядро задач на SQLite: состояния, зависимости, попытки, журнал, checkpoints, восстановление, тесты.
- **acceptance_criteria**: SPEC.md, раздел 7.
- **source_commit**: нет (код ещё не в git; ZIP `future-core.zip` от 2026-10-13)
- **current_state**: реализовано; 35/35 тестов на Python 3.10/3.12/3.13 в среде Claude; ждёт приёмки владельцем и прогона в GitHub Actions.
- **completed_steps**: спецификация; схема v1; автомат состояний; CRUD; зависимости и циклы; журнал; попытки и backoff; checkpoints; recover с reconciler; идемпотентность; handoff; CLI; тесты; README; workflow Actions.
- **pending_steps**: загрузить в репозиторий; увидеть зелёный прогон Actions; приёмка владельцем; затем этап B.
- **decisions_and_rationale**: см. SPEC.md, раздел 3 (кооперативная отмена RUNNING; BLOCKED при отменённой зависимости; restart_failed даёт новый бюджет; недоступный провайдер не тратит попытки; side_effects=external → BLOCKED после сбоя; родитель зависит от подзадач; один источник истины — SQLite; без FastAPI на этапе A).
- **changed_files**: все файлы проекта новые (см. README, «Структура»).
- **test_commands**: `python -m pip install -r requirements-dev.txt && python -m pytest -v && python -m taskcore demo`
- **test_results**: 35 passed (3.10.20, 3.12.3, 3.13.16), DEMO OK — 2026-10-13, среда Claude. GitHub Actions — NOT RUN.
- **known_errors**: нет известных падающих тестов.
- **risks**: бесплатные тарифы меняются; SQLite-файл на эфемерных серверах (Actions) не сохраняется между запусками — для этапов B–I нужен способ хранить базу (коммит в git или внешнее хранилище, решить на этапе C).
- **constraints**: бюджет 0; только iPhone/Safari; не подключать платное без разрешения; никаких секретов в репо, базе, логах; не заявлять «тест прошёл» без запуска.
- **artifact_references**: `/mnt/project-files/future-core/`, `/mnt/project-files/future-core.zip`
- **exact_next_action**: после приёмки — спроектировать этап B: минимальный HTTP API поверх `TaskCore` и одностраничную панель (список задач, кнопки resume/cancel/restart), с критериями приёмки до кода.
- **required_output_format**: формат из раздела 14 ТЗ владельца (цель, решение, структура, полный код, команды, тесты, факт. результаты, iPhone-инструкция, риски, критерии, PROJECT_STATE, handoff).

> Секреты, пароли и API-ключи в пакет не включаются.

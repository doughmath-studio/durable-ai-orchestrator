# HANDOFF — future / этап B (работа по расписанию через GitHub)

- **project_id**: future
- **task_id**: stage-B
- **task_goal**: система работает без участия человека и бесплатно: Issue владельца → задача → GitHub Models → ответ комментарием; база SQLite хранится в ветке taskcore-state.
- **acceptance_criteria**: тесты зелёные в GitHub Actions; настоящее Issue владельца получает ответ без участия человека; повторный такт не дублирует ответ.
- **source_commit**: этап A влит в main (PR #1); этап B — ветка stage-b-runner.
- **current_state**: этап A принят владельцем 2026-10-13; этап B реализован, 43 теста проходят локально.
- **completed_steps**: taskcore/github.py (Issues и GitHub Models на stdlib), taskcore/runner.py (такт), команда `tick`, tests/test_runner.py (8 тестов), workflow runner.yml с хранением базы в ветке taskcore-state.
- **pending_steps**: прогон в GitHub Actions; проверка на настоящем Issue.
- **decisions_and_rationale**: этап A — см. SPEC.md §3. Этап B: вместо веб-панели — Issues (бесплатного хостинга нет, Issues удобны в Safari); исполнитель — GitHub Models по GITHUB_TOKEN (без ключей); база — коммит в ветку taskcore-state (история = бэкапы); такты не пересекаются (concurrency); задачи только от владельца; отчёт в Issue сверяется по метке в комментарии.
- **changed_files**: taskcore/github.py, taskcore/runner.py, taskcore/__main__.py, tests/test_runner.py, .github/workflows/runner.yml, README.md, PROJECT_STATE.md, HANDOFF.md.
- **test_commands**: `python -m pip install -r requirements-dev.txt && python -m pytest -v && python -m taskcore demo`
- **test_results**: 43 passed локально (Python 3.12), 2026-10-13. GitHub Actions — см. PR этапа B.
- **known_errors**: нет известных падающих тестов.
- **risks**: бесплатные лимиты GitHub Models; расписание GitHub может запаздывать или выключиться после 60 дней без активности; репозиторий открытый — данные задач публичны.
- **constraints**: бюджет 0; только iPhone/Safari; не подключать платное без разрешения; никаких секретов в репо, базе, логах; не заявлять «тест прошёл» без запуска.
- **artifact_references**: `/mnt/project-files/future-core/`, `/mnt/project-files/future-core.zip`
- **exact_next_action**: повтор FAILED/BLOCKED задач из Issue (например, комментарием), затем этап C по плану владельца.
- **required_output_format**: формат из раздела 14 ТЗ владельца (цель, решение, структура, полный код, команды, тесты, факт. результаты, iPhone-инструкция, риски, критерии, PROJECT_STATE, handoff).

> Секреты, пароли и API-ключи в пакет не включаются.

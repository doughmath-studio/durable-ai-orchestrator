# HANDOFF — future / этап B (работа по расписанию через GitHub)

- **project_id**: future
- **task_id**: stage-B
- **task_goal**: система работает без участия человека и бесплатно: Issue владельца → задача → Groq (запасной HuggingFace) → ответ комментарием; база SQLite хранится в ветке taskcore-state.
- **acceptance_criteria**: тесты зелёные в GitHub Actions; настоящее Issue владельца получает ответ без участия человека; повторный такт не дублирует ответ.
- **source_commit**: этап A влит в main (PR #1); этап B — ветка stage-b-runner.
- **current_state**: этап A принят владельцем 2026-10-13; этап B реализован, 48 тестов проходят локально.
- **completed_steps**: taskcore/github.py (Issues, Groq/HuggingFace с подстраховкой, на stdlib), taskcore/runner.py (такт), команда `tick`, tests/test_runner.py (13 тестов), повтор задачи комментарием «повтори», workflow runner.yml с хранением базы в ветке taskcore-state.
- **pending_steps**: прогон в GitHub Actions; проверка на настоящем Issue.
- **decisions_and_rationale**: этап A — см. SPEC.md §3. Этап B: вместо веб-панели — Issues (бесплатного хостинга нет, Issues удобны в Safari); исполнитель — Groq, запасной HuggingFace (бесплатный вариант из первоначального кода владельца; GitHub Models закрыт в 2026 и отвечает «OK» вместо ответа — проверено 2026-10-13); ключи только в секретах GitHub; без ключа задачи ждут, попытки не тратятся; база — коммит в ветку taskcore-state (история = бэкапы); такты не пересекаются (concurrency); задачи только от владельца; отчёт в Issue сверяется по метке в комментарии.
- **changed_files**: taskcore/github.py, taskcore/runner.py, taskcore/__main__.py, tests/test_runner.py, .github/workflows/runner.yml, README.md, PROJECT_STATE.md, HANDOFF.md.
- **test_commands**: `python -m pip install -r requirements-dev.txt && python -m pytest -v && python -m taskcore demo`
- **test_results**: 48 passed локально (Python 3.12), 2026-10-13. GitHub Actions — см. PR этапа B.
- **known_errors**: нет известных падающих тестов.
- **risks**: бесплатные лимиты Groq/HuggingFace и смена названий моделей (меняются через GROQ_MODEL/HF_MODEL); расписание GitHub может запаздывать или выключиться после 60 дней без активности; репозиторий открытый — данные задач публичны.
- **constraints**: бюджет 0; только iPhone/Safari; не подключать платное без разрешения; никаких секретов в репо, базе, логах; не заявлять «тест прошёл» без запуска.
- **artifact_references**: `/mnt/project-files/future-core/`, `/mnt/project-files/future-core.zip`
- **exact_next_action**: дождаться секрета GROQ_API_KEY и проверить ответ на настоящем Issue; затем этап C по плану владельца.
- **required_output_format**: формат из раздела 14 ТЗ владельца (цель, решение, структура, полный код, команды, тесты, факт. результаты, iPhone-инструкция, риски, критерии, PROJECT_STATE, handoff).

> Секреты, пароли и API-ключи в пакет не включаются.

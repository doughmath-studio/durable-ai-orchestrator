# Спецификация — Этап A: Durable Task Core

## 1. Цель

Надёжное ядро задач: задачи, их зависимости, попытки выполнения, журнал событий,
контрольные точки и восстановление после сбоя. Всё хранится в одном файле SQLite,
который является **единственным источником истины**. ИИ-провайдеры не подключены.

## 2. Состав

| Модуль | Файл | Что делает |
|---|---|---|
| Task Store + Durable Queue | `taskcore/core.py` | задачи, очередь READY, атомарный захват |
| State machine | `taskcore/core.py` (`TRANSITIONS`, `_transition`) | все смены статуса в одном месте |
| Event Log | таблица `events` | только дописывается (триггеры запрещают UPDATE/DELETE) |
| Retry Manager | `backoff_delay`, `_handle_failure` | экспоненциальная задержка + jitter, лимит попыток |
| Checkpoint Manager | `save_checkpoint`, `load_checkpoint` | версии + SHA-256, пропуск повреждённых |
| Artifact Store (метаданные) | `add_artifact` | ссылки на файлы с контрольной суммой |
| Recovery Manager | `recover` | закрывает прерванные попытки, безопасно возвращает задачи |
| Provider registry (заготовка) | таблица `providers` | доступность и возможности, без секретов |
| Verification (минимум) | `verify`, `basic_verifier` | без проверки COMPLETED невозможен |
| Handoff | `taskcore/handoff.py` | пакет передачи задачи другому ИИ |
| Migrations | `taskcore/migrations.py`, `db.py` | версия схемы в `PRAGMA user_version` |

Не входит в этап A: веб-API, панель, реальные модели, бюджет в деньгах, бэкапы.

## 3. Найденные противоречия в исходной архитектуре и решения

1. **Нельзя отменить выполняющуюся задачу.** В исходной таблице переходов у RUNNING и
   VERIFYING нет перехода в CANCELLED, хотя требуется «корректная отмена».
   *Решение:* добавлены RUNNING → CANCELLED и VERIFYING → CANCELLED, но RUNNING
   отменяется только кооперативно: `cancel()` ставит флаг `cancel_requested`, задача
   становится CANCELLED, когда текущая попытка закончится. Внешнее действие посреди
   выполнения не обрывается вслепую.
2. **Что делать с задачей, если её зависимость отменена?** В спецификации она ждала бы
   вечно. *Решение:* такая задача уходит в BLOCKED с причиной и условием возобновления.
3. **Ручной перезапуск FAILED при исчерпанных попытках** немедленно снова дал бы FAILED.
   *Решение:* `restart_failed()` — явное действие, обнуляет счётчик текущего бюджета,
   увеличивает `restart_count`, вся история попыток сохраняется.
4. **«Недоступность провайдера» не должна сжигать попытки.** Если провайдера нет ещё до
   запуска, задача остаётся READY, попытка не тратится, в журнал пишется одно событие
   (детерминированный `event_id`, без спама). Если провайдер отвалился во время
   попытки — это временная ошибка с обычным лимитом.
5. **Повтор после сбоя небезопасен для внешних действий.** Добавлено поле задачи
   `side_effects` (`none` / `idempotent` / `external`). После сбоя:
   `none`/`idempotent` → обычный повтор; `external` → BLOCKED, если нет функции
   сверки (`reconciler`), которая подтвердит фактический результат.
6. **Родительская задача и подзадачи.** Чтобы родитель не стал COMPLETED раньше детей:
   подзадача, созданная для родителя в PENDING/BLOCKED, автоматически становится его
   зависимостью; `verify()` также отказывается завершать родителя с незавершёнными детьми.
7. **Несколько источников истины.** JSONL-журнал отдельно от базы не делается: журнал —
   таблица `events` в той же транзакции, что и смена состояния. Экспорт — этап C.
8. **FastAPI в первой версии не нужен** — ядро работает без него; ноль внешних зависимостей
   у ядра, pytest нужен только для тестов.

## 4. Изменения в модели данных (по сравнению с предложенной)

Добавлено:
- `tasks.request_hash` — отличает повтор того же запроса от конфликта по `idempotency_key`.
- `tasks.side_effects`, `tasks.required_capability`, `tasks.cancel_requested`,
  `tasks.restart_count`, `tasks.blocked_reason`, `tasks.resume_condition`.
- `task_attempts.attempt_uid` (correlation_id для событий попытки), `lease_expires_at`
  (аренда: долгие задачи продлевают её `heartbeat()`), `error_kind`.
- `providers.unavailable_until`.

Ограничения в базе (CHECK/UNIQUE/FK):
- статус только из списка; BLOCKED без причины невозможен;
- `1 ≤ max_attempts ≤ 20`, `attempt_count ≤ max_attempts` — бесконечный повтор
  невозможен даже при ошибке в коде;
- не больше одной выполняющейся попытки на задачу (частичный уникальный индекс);
- задача не зависит сама от себя; циклы проверяются рекурсивным запросом;
- `events.event_id` уникален; события нельзя изменить или удалить (триггеры);
- внешние ключи включены (`PRAGMA foreign_keys = ON`), WAL, `synchronous = FULL`.

Секреты: в `providers` запрещены поля/значения, похожие на ключи (`api_key`, `token`,
`sk-…` и т.п.) — `SecretDetectedError`.

## 5. Конечный автомат

```
PENDING    → READY, BLOCKED, CANCELLED
READY      → RUNNING, BLOCKED, CANCELLED
RUNNING    → VERIFYING, RETRY_WAIT, BLOCKED, FAILED, CANCELLED*
VERIFYING  → COMPLETED, RETRY_WAIT, BLOCKED, FAILED, CANCELLED*
RETRY_WAIT → READY, BLOCKED, CANCELLED
BLOCKED    → PENDING, READY, CANCELLED
FAILED     → PENDING (только restart_failed), CANCELLED
COMPLETED, CANCELLED — терминальные
* добавлено, см. п. 3.1
```

Кто делает переход:

| Переход | Операция |
|---|---|
| PENDING → READY, RETRY_WAIT → READY | `promote_ready()` (проверяет зависимости и время) |
| READY → RUNNING | `claim_next()` — атомарно, создаёт попытку |
| RUNNING → VERIFYING / RETRY_WAIT / FAILED / BLOCKED | `finish_attempt()` |
| VERIFYING → COMPLETED / … | `verify()` |
| → BLOCKED вручную | `block(reason)` |
| BLOCKED → READY/PENDING | `resume()` |
| FAILED → PENDING | `restart_failed()` |
| → CANCELLED | `cancel()` |

Общий `transition()` не может делать переходы, принадлежащие попыткам и проверке.
Каждая смена статуса — `UPDATE … WHERE id=? AND status=<старый>` (защита от гонок) и
событие `status_changed` в той же транзакции.

Ошибки исполнителя:
- `TransientError` (и любые неизвестные исключения) → RETRY_WAIT с задержкой
  `min(cap, base·2^(n−1)) · (0.5…1.0)`; при `attempt_count = max_attempts` → FAILED;
- `PermanentError` → FAILED сразу;
- `NeedsHumanError` → BLOCKED с причиной.

## 6. Восстановление (`recover`)

Запускать при каждом старте процесса: `recover(force=True)`. Без `force` — только
попытки с истёкшей арендой.

1. Находит попытки в статусе `running`.
2. Пишет событие `recovery` (одно на попытку, `event_id = recovery:<id>`).
3. Читает последний **целый** checkpoint и историю попытки.
4. Решает:
   - есть `reconciler` → он говорит `done` (результат принимается → VERIFYING, повторного
     выполнения нет), `not_done` (обычный повтор) или `unknown` (→ BLOCKED);
   - нет `reconciler`, `side_effects = external` → BLOCKED («неизвестно, выполнено ли»);
   - иначе → RETRY_WAIT (или FAILED, если попытки кончились).
5. Завершённые подзадачи и их результаты не трогаются.
6. Поздний ответ от «зомби»-попытки отклоняется (`StaleAttemptError`) и сохраняется в
   журнал как `late_result_ignored` для ручной сверки.

## 7. Критерии приёмки этапа A

| # | Критерий | Как проверить |
|---|---|---|
| 1 | Схема создаётся миграцией, повторный запуск ничего не ломает | `test_schema_migration_is_idempotent` |
| 2 | Все 20 сценариев раздела 10 ТЗ покрыты тестами | `docs/TESTS.md` |
| 3 | `python -m pytest` — все тесты зелёные на Python 3.10+ | локально и в GitHub Actions |
| 4 | `python -m taskcore demo` печатает `DEMO OK` | вручную или в Actions |
| 5 | Нет внешних зависимостей у ядра, нет секретов в коде | `requirements.txt` пуст, `.gitignore` |
| 6 | Задача не может стать COMPLETED без verify | `test_06`, `test_verification_failure_is_not_completed` |
| 7 | Внешнее действие не повторяется вслепую после сбоя | `test_15b`, `test_15c` |
| 8 | Тесты запущены в облаке, доступном с iPhone | зелёная галочка в Actions — **ждёт владельца** |

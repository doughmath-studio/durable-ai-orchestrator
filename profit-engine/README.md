# PROFIT ENGINE — MVP

Автономный поиск коммерческих возможностей на Cloudflare Workers + D1 (бесплатный тариф).
Система **ищет, проверяет, оценивает и готовит черновики** продуктов и предложений.
Она **ничего не отправляет клиентам, не публикует, не покупает и не тратит деньги**.

> Ориентир 300 PLN/день — гипотеза. Суммы в системе — это потенциальные значения из источников
> или оценки, а не заработанные деньги. Доход появляется только после подтверждённой оплаты.

## Что внутри

| Модуль | Файл | Что делает |
|---|---|---|
| SCOUT | `src/orchestrator.js` (`pickSources`) | выбирает источники для запуска; запрещённые условиями никогда не загружаются |
| COLLECTOR | `src/agents/collector.js` | RSS 2.0, Atom 1.0, TED API v3 → единый формат |
| VALIDATOR | `src/agents/validator.js` | источник проверен, условия разрешают, https, дата, дедлайн |
| BUYER FINDER | `src/agents/buyer.js` | покупатель, путь контакта, доказательство спроса (`confirmed` / `indicated` / `unknown`) |
| VALUATOR | `src/agents/valuator.js` | выручка (только если сумма указана в источнике), затраты, маржа, скоринг |
| PRODUCTIZER + SALES PLANNER | `src/agents/productizer.js` | черновик продукта, план продажи, черновик письма (**НЕ отправляется**) |
| RISK CONTROL | `src/agents/risk.js` + дедупликация в оркестраторе | мошенничество, спам, персональные данные, дубли |
| ORCHESTRATOR | `src/orchestrator.js` | один запуск: блокировка от параллельных запусков, журнал, ошибки, уведомления |
| AI (необязательно) | `src/ai.js` | OmniRoute `/v1/chat/completions`, выключен по умолчанию |

Хранилище: D1 (`migrations/0001_init.sql`): `sources`, `opportunities`, `runs`, `events`.

## API

| Метод | Путь | Доступ |
|---|---|---|
| GET | `/` | дашборд для iPhone |
| GET | `/health` | всегда открыт |
| GET | `/opportunities?page=1&limit=20&status=ready_for_commercial_check&min_score=50` | чтение |
| GET | `/opportunities/:id` (`?view=html` — страница) | чтение |
| GET | `/opportunities.csv` (те же фильтры, до 1000 строк) | чтение |
| GET | `/sources`, `/runs` | чтение |
| POST | `/run` | только с `RUN_TOKEN` (заголовок `Authorization: Bearer …` или форма на дашборде) |

Чтение открыто, пока не задан секрет `READ_TOKEN`. Если задан — откройте один раз
`https://…workers.dev/?key=ВАШ_READ_TOKEN`, браузер запомнит доступ (cookie на 30 дней).

Cron: `0 */6 * * *` (каждые 6 часов, UTC).

## Скоринг (прозрачный)

`score = Σ вес × значение` по известным компонентам, максимум 100.
`confidence = сумма весов известных компонентов / 100`.

| Компонент | Вес | Значение |
|---|---|---|
| demand | 30 | confirmed 1 · indicated 0.5 · unknown → **неизвестно** |
| monetization | 20 | ясность способа продажи по категории (0.4–0.8) |
| value | 20 | (сумма из источника в PLN ÷ часы) ÷ 100 PLN/ч, не больше 1; нет суммы → **неизвестно** |
| feasibility | 15 | сложность low 1 · medium 0.6 · high 0.25 |
| freshness | 10 | 1 → 0 линейно за `MAX_ITEM_AGE_DAYS`; нет даты → **неизвестно** |
| risk | 5 | low 1 · medium 0.5 · high 0 |

Неизвестный компонент даёт 0 баллов и снижает `confidence` — он никогда не считается подтверждённым.
Разбивка хранится в каждой записи (`score_breakdown`).

Статусы: `rejected` (не прошла проверку или высокий риск) → `validated` → `ready_for_commercial_check`
(спрос `confirmed` и score ≥ `READY_SCORE_THRESHOLD`, по умолчанию 55).

## Источники

Реестр — `src/sources.js`. Каждый источник стартует как `unverified`; первый запуск в Cloudflare
проверяет доступность и формат и записывает результат в `/sources`.

| Источник | Условия | Поведение |
|---|---|---|
| TED API v3 (тендеры ЕС: IT, ПО, бизнес-услуги) | открытые данные ЕС, нужна ссылка на источник | собирается |
| Grants.gov RSS | public domain (США) | собирается |
| Hacker News через hnrss.org | ссылка на оригинал | собирается |
| Remote OK RSS | ссылка на оригинал и Remote OK | собирается |
| We Work Remotely RSS | условия не подтверждены | только проверка доступности |
| Reddit r/forhire | условия Reddit ограничивают коммерческое использование | **не загружается** |

Свои фиды: переменная `FEEDS` — JSON-массив https-адресов, условия которых вы проверили.

**Честно:** из среды сборки внешняя сеть закрыта, поэтому ни один источник не загружался при
сборке. Формат запроса TED взят из документации TED API v3 по памяти — если TED вернёт ошибку,
она будет видна в `/sources` (`last_error`) и запрос поправим. Запрос TED можно заменить через `TED_QUERY`.

## OmniRoute (необязательно)

Проверено по `docs/reference/API_REFERENCE.md` репозитория OmniRoute (v3.8.x): `POST /v1/chat/completions`,
`Authorization: Bearer <ключ OmniRoute>`, модель вида `provider/model` или имя combo.
OmniRoute — **ваш собственный сервер** (по умолчанию `http://localhost:20128`). Worker в Cloudflare
до localhost не достанет: нужен публичный HTTPS-адрес. Пока его нет — AI выключен, всё остальное работает.

Включение: `OMNIROUTE_BASE_URL`, `OMNIROUTE_MODEL`, `AI_ENABLED="true"` в `wrangler.toml`,
ключ — только `npx wrangler secret put OMNIROUTE_API_KEY`. AI не меняет цифры и статусы, только добавляет заметки.

## Лимиты бесплатного тарифа

- ≤ 8 источников и ≤ 25 записей с каждого за запуск (лимит 50 внешних подзапросов);
- запись в D1 пачками через `json_each` — около 10 запросов D1 за запуск (лимит 50);
- таймаут 10 с и до 3 попыток только для временных ошибок (сеть, 429, 5xx);
- запуски не пересекаются (блокировка в таблице `runs`, зависший запуск снимается через 20 мин);
- риск: лимит CPU бесплатного тарифа (10 мс). Разбор фидов лёгкий, но реальную нагрузку покажет
  вкладка Observability после первого запуска.

## Уведомления

Только критические события (все источники упали, запуск сломался): видны на дашборде.
Telegram — по желанию, секреты `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID`.

## Команды

```bash
cd profit-engine
npm ci                     # зависимости (только wrangler)
npm test                   # 12 тестов, Node 22+
npm run check              # сборка Worker без публикации
npm run db:migrate:local   # схема в локальную D1
npx wrangler dev --var RUN_TOKEN:local-token-123456   # локальный запуск
```

## Развёртывание с iPhone (через GitHub Actions, бесплатно)

1. Cloudflare: зарегистрируйтесь на dash.cloudflare.com и один раз откройте **Workers & Pages**
   (Cloudflare попросит выбрать поддомен `*.workers.dev`).
2. Cloudflare → **My Profile → API Tokens → Create Token** → шаблон **Edit Cloudflare Workers** →
   добавьте строку **Account · D1 · Edit** → Create. Скопируйте токен.
3. Cloudflare → **Workers & Pages** → справа **Account ID** — скопируйте.
4. GitHub → репозиторий → **Settings → Secrets and variables → Actions → New repository secret**:
   `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `RUN_TOKEN` (любая длинная фраза, ≥ 20 символов —
   это пароль для ручного запуска).
5. GitHub → **Actions → profit-engine-deploy → Run workflow**. Workflow сам создаст D1, применит схему,
   опубликует Worker, установит `RUN_TOKEN` и проверит `/health`. Адрес Worker будет в логе шага «Check /health».

Workflow видны в списке Actions только после слияния в `main`.

Ручной вариант (компьютер с браузером):
```bash
npx wrangler login
npx wrangler d1 create profit-engine          # вставьте database_id в wrangler.toml
npx wrangler d1 migrations apply profit-engine --remote
npx wrangler deploy
npx wrangler secret put RUN_TOKEN
```

## Безопасность

- Секреты только в Cloudflare Secrets / GitHub Secrets; в коде, git и логах их нет
  (`redact()` вычищает их из сообщений, тест это проверяет).
- `POST /run` закрыт токеном (сравнение за постоянное время); без `RUN_TOKEN` ручной запуск выключен.
- CSV защищён от формул (`=`, `+`, `-`, `@`), HTML экранируется, строгий CSP, без JavaScript.
- Нет обхода авторизации, нет сбора персональных данных, нет отправки сообщений.

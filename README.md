# Аренда пауэрбанков — ДЗ 1, команда 6

Архитектура и все решения описаны в [ADR-0001](docs/adr/0001-architecture.md). README — короткая выжимка для разработки.

## Сервисы

| Сервис | Папка | Порт | Ответственный |
|---|---|---|---|
| rental-api (2 инстанса за nginx) | `rental_api/` | 8080 через nginx | Иван |
| rental-worker | `rental_worker/` | 9100 (метрики) | друг |
| external-stubs | `external_stubs/` | 3629 | друг |
| PostgreSQL | `migrations/` | 5432 | Иван (схема) |
| Redis | — | 6379 | — |
| nginx, Prometheus, Grafana | `deploy/` | 8080, 9090, 3000 | — |

## Запуск

```bash
docker compose up --build
```

- Публичное API и Swagger: http://localhost:8080/docs. Внутренние ручки в Swagger видны, но снаружи nginx их не пропускает.
- Заглушки: http://localhost:3629/docs
- Prometheus: http://localhost:9090, Grafana: http://localhost:3000

Схема БД применяется из `migrations/` только при первом создании тома. После изменения схемы:

```bash
docker compose down -v && docker compose up --build
```

## Тесты rental-api

```bash
cd rental_api
python -m venv .venv
.venv/Scripts/activate        # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

## Договорённости между сервисами

Эти правила нельзя нарушать без обсуждения: на них держатся идемпотентность и кэш.

1. **`offers` и `orders` меняет только rental-api.** Worker таблицу `orders` только читает. Исключение: worker удаляет офферы без заказа старше суток.
2. **Любой `UPDATE orders`** в rental-api: `version = version + 1`, условие на ожидаемый статус (`WHERE status = ...`), после коммита — запись заказа в кэш.
3. **`payment_tasks`.** rental-api вставляет задачи `HOLD` и `CLEAR` в той же транзакции, что и изменение заказа. Worker их разбирает:
   1. вызывает payments с ключом `order_id`;
   2. вызывает `POST /internal/v1/orders/{order_id}/payment-result {kind, result}`;
   3. помечает задачу `DONE`.

   Если любой шаг не прошёл, задача остаётся `PENDING` с отложенным `next_attempt_at`.
4. **Зависшие заказы.** Worker ищет заказы в `CREATED` или `EJECTING` с `processing_until < now() - 60s` и вызывает `POST /internal/v1/orders/{order_id}/advance`. Логика доведения целиком в rental-api.
5. **Внутренние ручки** `/internal/*` доступны только внутри docker-сети через `nginx:8081`. Снаружи порт 8080 их не пропускает.
6. **Контракт заглушек** — раздел 5.4 ADR. Если меняете — сначала ADR, потом код.

## Задачи

### rental-api (Иван)

- [ ] `repo/`: offers, orders, payment_tasks (asyncpg, SQL)
- [ ] `clients/`: stations, tariffs (LRU), users (фоллбэк), payments, configs; таймауты, ретраи, circuit breaker, retry budget
- [ ] `services/`: создание оффера, начало аренды с lease и `202`, возврат, `advance`, `payment-result`
- [ ] `cache/`: Redis с версиями (Lua), singleflight, ограничитель чтений, L1 при отказе Redis
- [ ] `GET /orders/{id}` и список с курсором
- [ ] Интеграционные тесты идемпотентности и lease

### rental-worker и external-stubs (друг)

- [ ] `process_outbox`: `FOR UPDATE SKIP LOCKED`, экспоненциальная задержка, порядок HOLD → CLEAR
- [ ] `advance_stuck_orders`
- [ ] `cleanup_offers`
- [ ] Метрики worker: `outbox_pending`, `outbox_oldest_age_seconds`, `payment_debt_open`
- [ ] Заглушки: `POST /admin/faults` (ok, error, timeout, slow; задержка; доля ошибок) для каждого сервиса
- [ ] Заглушки: журнал выдач для проверки «одна банка на оффер»

### Не распределено

- [ ] Эмулятор нагрузки (locust) и три проверки корректности — раздел 11 ADR
- [ ] Дашборд Grafana
- [ ] Прогоны, графики, отчёт о тестировании

CREATE TABLE offers (
    id              uuid PRIMARY KEY,
    user_id         text        NOT NULL,
    station_id      text        NOT NULL,
    tariff_id       text        NOT NULL,
    price_per_hour  int         NOT NULL,
    free_period_min int         NOT NULL,
    deposit         int         NOT NULL,
    user_fallback   bool        NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL,
    expires_at      timestamptz NOT NULL,
    order_id        uuid        NULL
);
CREATE INDEX offers_created_at ON offers (created_at);

CREATE TABLE orders (
    id                uuid        PRIMARY KEY,
    offer_id          uuid        NOT NULL UNIQUE,
    user_id           text        NOT NULL,
    start_station_id  text        NOT NULL,
    finish_station_id text        NULL,
    powerbank_id      text        NULL,
    price_per_hour    int         NOT NULL,
    free_period_min   int         NOT NULL,
    deposit           int         NOT NULL,
    status            text        NOT NULL,
    payment_state     text        NOT NULL,
    failure_code      text        NULL,
    total_amount      int         NULL,
    processing_until  timestamptz NULL,
    version           int         NOT NULL DEFAULT 1,
    created_at        timestamptz NOT NULL,
    start_time        timestamptz NULL,
    finish_time       timestamptz NULL,
    CONSTRAINT orders_status_check
        CHECK (status IN ('CREATED', 'EJECTING', 'ACTIVE', 'FINISHED', 'FAILED')),
    CONSTRAINT orders_payment_state_check
        CHECK (payment_state IN ('NONE', 'HELD', 'DEBT', 'CLEARED'))
);
CREATE UNIQUE INDEX orders_active_powerbank ON orders (powerbank_id) WHERE status = 'ACTIVE';
CREATE INDEX orders_powerbank ON orders (powerbank_id, finish_time DESC);
CREATE INDEX orders_user ON orders (user_id, created_at DESC, id DESC);
CREATE INDEX orders_stuck ON orders (processing_until) WHERE status IN ('CREATED', 'EJECTING');

CREATE TABLE payment_tasks (
    id              bigserial   PRIMARY KEY,
    order_id        uuid        NOT NULL,
    user_id         text        NOT NULL,
    kind            text        NOT NULL,
    amount          int         NOT NULL,
    status          text        NOT NULL,
    attempts        int         NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL,
    created_at      timestamptz NOT NULL,
    UNIQUE (order_id, kind),
    CONSTRAINT payment_tasks_kind_check CHECK (kind IN ('HOLD', 'CLEAR')),
    CONSTRAINT payment_tasks_status_check CHECK (status IN ('PENDING', 'DONE', 'FAILED'))
);
CREATE INDEX payment_tasks_pending ON payment_tasks (next_attempt_at) WHERE status = 'PENDING';

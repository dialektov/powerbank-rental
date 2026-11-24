from prometheus_client import Counter, Gauge, Histogram

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 5.0)

HTTP_REQUESTS = Counter("http_requests_total", "Incoming requests", ["handle", "code"])
HTTP_DURATION = Histogram(
    "http_request_duration_seconds", "Incoming request latency", ["handle"], buckets=_LATENCY_BUCKETS
)

DEPENDENCY_REQUESTS = Counter(
    "dependency_requests_total", "Calls to dependencies", ["dep", "result"]
)
DEPENDENCY_DURATION = Histogram(
    "dependency_duration_seconds", "Dependency call latency", ["dep"], buckets=_LATENCY_BUCKETS
)

USERS_FALLBACK = Counter("users_fallback_total", "User profile fallbacks")
TARIFF_CACHE = Counter("tariff_cache_total", "Tariff cache lookups", ["result"])
CONFIG_AGE = Gauge("config_age_seconds", "Age of the last successfully loaded config")

ORDER_CACHE = Counter("order_cache_total", "Order cache lookups", ["result"])
ORDER_CACHE_WRITES = Counter("order_cache_write_total", "Order cache writes", ["result"])
SINGLEFLIGHT_SHARED = Counter(
    "singleflight_shared_total", "Requests served by another in-flight DB read"
)
DB_READ_LIMITER_REJECTED = Counter(
    "db_read_limiter_rejected_total", "Reads rejected by the DB read limiter"
)

ORDERS = Counter("orders_total", "Orders by resulting status", ["status"])
ORDERS_IN_PROGRESS_RESPONSES = Counter(
    "orders_in_progress_responses_total", "202 responses for orders being processed"
)
ORDERS_LEASE_TAKEOVER = Counter(
    "orders_lease_takeover_total", "Lease takeovers on stuck orders", ["by"]
)

DB_POOL_IN_USE = Gauge("db_pool_in_use", "Connections in use", ["pool"])

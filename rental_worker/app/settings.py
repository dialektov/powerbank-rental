from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://rental:rental@localhost:5432/rental"
    rental_internal_url: str = "http://localhost:8081"
    stubs_url: str = "http://localhost:3629"
    metrics_port: int = 9100
    metrics_interval_sec: float = 5.0

    outbox_interval_sec: float = 1.0
    outbox_batch_size: int = 100
    outbox_concurrency: int = Field(default=4, ge=1)
    payments_timeout_sec: float = 2.0
    payments_max_backoff_sec: float = 600.0

    stuck_interval_sec: float = 10.0
    stuck_after_sec: float = 60.0

    cleanup_interval_sec: float = 300.0
    offers_retention_sec: float = 86400.0

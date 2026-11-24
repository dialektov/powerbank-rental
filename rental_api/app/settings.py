import socket

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Инфраструктурные настройки из окружения. Бизнес-параметры приходят из сервиса configs."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    instance_id: str = Field(default_factory=socket.gethostname)

    database_url: str = "postgresql://rental:rental@localhost:5432/rental"
    db_pool_size: int = 10
    db_read_pool_size: int = 8
    db_read_acquire_timeout_sec: float = 0.05

    redis_url: str = "redis://localhost:6379/0"
    redis_timeout_sec: float = 0.02

    stubs_url: str = "http://localhost:3629"

    cursor_secret: str = "dev-cursor-secret"
    cursor_ttl_sec: int = 86400

    config_defaults_path: str = "config.defaults.json"
    config_refresh_sec: float = 60

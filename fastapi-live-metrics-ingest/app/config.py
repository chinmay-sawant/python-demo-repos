from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/metrics"
    database_echo: bool = False
    pool_size: int = 10
    max_overflow: int = 5
    pool_timeout: int = 30
    pool_pre_ping: bool = True
    pool_recycle: int = 1800
    statement_timeout_ms: int = 30000
    ingest_max_batch_size: int = 1000
    ingest_max_label_length: int = 256
    ingest_max_body_size: int = 1_048_576
    retention_hours: int = 24
    vendor_export_url: str = ""
    vendor_api_key: str = ""
    vendor_max_concurrency: int = 5
    vendor_timeout_seconds: int = 10
    vendor_retry_max_attempts: int = 3

    model_config = {"env_prefix": "METRICS_", "env_file": ".env"}

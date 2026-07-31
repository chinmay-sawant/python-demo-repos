import os
from typing import ClassVar


class Config:
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", "postgresql://postgres@localhost/relay")
    SQLALCHEMY_ENGINE_OPTIONS: ClassVar[dict[str, object]] = {
        "pool_size": 10,
        "max_overflow": 5,
        "pool_pre_ping": True,
    }
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 256 * 1024  # Flask enforces this before route code runs
    INGEST_API_KEY = os.getenv("INGEST_API_KEY", "dev-api-key")
    INGEST_MAX_PAYLOAD_SIZE = 256 * 1024  # 256KB
    INGEST_MAX_EVENT_BODY_BYTES = 262144
    DELIVERY_TIMEOUT_CONNECT = int(os.getenv("DELIVERY_TIMEOUT_CONNECT", "10"))
    DELIVERY_TIMEOUT_READ = int(os.getenv("DELIVERY_TIMEOUT_READ", "30"))
    DELIVERY_MAX_RETRIES = int(os.getenv("DELIVERY_MAX_RETRIES", "5"))
    DELIVERY_RETRY_BASE_DELAY = int(os.getenv("DELIVERY_RETRY_BASE_DELAY", "60"))
    DELIVERY_MAX_CONCURRENCY = int(os.getenv("DELIVERY_MAX_CONCURRENCY", "10"))
    DELIVERY_QUEUE_POLL_INTERVAL = int(os.getenv("DELIVERY_QUEUE_POLL_INTERVAL", "5"))
    DELIVERY_CLAIM_BATCH_SIZE = int(os.getenv("DELIVERY_CLAIM_BATCH_SIZE", "50"))
    RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "30"))

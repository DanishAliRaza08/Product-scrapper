"""Application settings, loaded from environment variables prefixed with ``MPI_``."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MPI_", env_file=".env", extra="ignore")

    env: str = "development"
    app_name: str = "Multi-Marketplace Product Intelligence"
    api_prefix: str = "/api/v1"

    database_url: str = "postgresql+psycopg://mpi:mpi@localhost:5432/mpi"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    redis_url: str = "redis://localhost:6379/0"

    # Security
    jwt_secret: SecretStr = SecretStr("dev-only-change-me-dev-only-change-me")
    jwt_issuer: str = "mpi"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    cursor_secret: SecretStr = SecretStr("dev-only-cursor-secret")
    download_link_minutes: int = 15
    secure_cookies: bool = False
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "tauri://localhost", "http://tauri.localhost"]
    )
    login_rate_limit_per_minute: int = 10
    allow_self_signup: bool = True

    # Secrets: "local" encrypts credentials into a file outside SQL (development);
    # production deployments plug in a cloud secrets manager (see app/core/secrets.py).
    secrets_backend: str = "local"
    secrets_encryption_key: SecretStr | None = None
    secrets_dir: Path = BACKEND_ROOT / "var" / "secrets"

    # Object storage: "local" filesystem or "s3" (S3-compatible endpoint).
    storage_backend: str = "local"
    storage_dir: Path = BACKEND_ROOT / "var" / "storage"
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str | None = None

    # Workers / jobs
    celery_eager: bool = False
    job_discovery_page_limit: int = 200
    job_detail_batch_size: int = 25
    job_max_item_attempts: int = 3
    job_retry_base_seconds: float = 2.0
    refresh_stale_after_hours: int = 24
    export_retention_days: int = 7
    observation_partition_months_ahead: int = 3

    # Connectors
    enable_demo_connector: bool = True
    allow_private_network_urls: bool = False
    http_timeout_seconds: float = 30.0

    ebay_client_id: str | None = None
    ebay_client_secret: SecretStr | None = None
    ebay_ru_name: str | None = None
    ebay_environment: str = "production"  # or "sandbox"

    amazon_lwa_client_id: str | None = None
    amazon_lwa_client_secret: SecretStr | None = None
    amazon_spapi_app_id: str | None = None
    amazon_research_refresh_token: SecretStr | None = None

    public_web_url: str = "http://localhost:3000"
    public_api_url: str = "http://localhost:8000"

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()

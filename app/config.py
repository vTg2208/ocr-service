"""
Application configuration.

All settings are read from environment variables (via a .env file in local
development). This keeps the service configurable without code changes,
which matters for deployment on platforms like Render or Railway.
"""

from functools import lru_cache
from typing import List

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- General ---
    app_name: str = "AranyaSetu"
    log_level: str = "INFO"
    environment: str = "development"

    # --- File validation ---
    max_file_size_mb: int = 10
    fra_archive_max_batch_files: int = 20
    fra_archive_max_batch_total_mb: int = 100
    allowed_extensions: List[str] = ["jpg", "jpeg", "png", "bmp", "tif", "tiff", "pdf"]

    # --- OCR engine ---
    paddleocr_detection_model_name: str = "PP-OCRv5_mobile_det"
    paddleocr_recognition_model_name: str = "ta_PP-OCRv5_mobile_rec"

    # --- Image preprocessing ---
    min_image_width: int = 1200

    # --- PDF processing ---
    pdf_dpi: int = 300

    # --- LLM Engine (Groq) ---
    llm_api_key: str = ""
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model_name: str = "openai/gpt-oss-120b"

    # --- Supporting cadastral evidence registry ---
    database_url: str = "sqlite+pysqlite:///./aranyasetu.db"
    secure_upload_dir: str = "private_uploads"
    area_tolerance_percent: float = 10.0
    overlap_min_sqm: float = 1.0
    overlap_min_percent: float = 1.0
    automatic_match_confidence: float = 0.85
    auth_secret: str = "change-me-in-production"
    auth_issuer: str = "aranyasetu"
    auth_audience: str = "aranyasetu-api"
    demo_auth_enabled: bool = True
    demo_access_code: str = "1234"
    demo_session_minutes: int = 480
    upload_storage_backend: str = "local"
    s3_bucket: str = ""
    s3_prefix: str = "fra-evidence"
    clamav_host: str = ""
    clamav_port: int = 3310
    malware_scan_required: bool = False
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60

    # --- Historical imagery discovery (supporting evidence only) ---
    stac_endpoint: str = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
    stac_allowed_hosts: List[str] = ["planetarycomputer.microsoft.com"]
    stac_allowed_collections: List[str] = ["landsat-c2-l2"]
    stac_timeout_seconds: float = 20
    stac_max_pages: int = 5
    stac_max_results: int = 100
    stac_max_cloud: float = 40

    # --- Current satellite analysis-ready ingestion ---
    satellite_stac_endpoint: str = "https://earth-search.aws.element84.com/v1/search"
    satellite_stac_allowed_hosts: List[str] = ["earth-search.aws.element84.com"]
    satellite_stac_allowed_collections: List[str] = ["sentinel-2-l2a"]
    satellite_asset_allowed_hosts: List[str] = [
        "sentinel-cogs.s3.us-west-2.amazonaws.com",
        "e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com",
    ]
    satellite_default_band_keys: List[str] = ["green", "nir"]
    satellite_max_output_pixels: int = 4_000_000

    # --- Durable FRA background processing ---
    job_lease_seconds: int = 300
    job_heartbeat_seconds: int = 30
    job_retry_base_seconds: int = 15
    job_retry_max_seconds: int = 900
    job_poll_seconds: float = 2.0


    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @model_validator(mode="after")
    def validate_production_safeguards(self):
        if self.job_lease_seconds < 2:
            raise ValueError("JOB_LEASE_SECONDS must be at least two seconds.")
        if not 0 < self.job_heartbeat_seconds < self.job_lease_seconds:
            raise ValueError("JOB_HEARTBEAT_SECONDS must be positive and shorter than the lease.")
        if self.job_retry_base_seconds < 0 or self.job_retry_max_seconds < self.job_retry_base_seconds:
            raise ValueError("Job retry delays must be non-negative and bounded in ascending order.")
        if self.job_poll_seconds <= 0:
            raise ValueError("JOB_POLL_SECONDS must be positive.")
        if self.environment.casefold() != "production":
            return self
        normalized_secret = self.auth_secret.casefold()
        if len(self.auth_secret) < 32 or normalized_secret.startswith(("change-", "replace-")):
            raise ValueError("Production AUTH_SECRET must contain at least 32 non-default characters.")
        if self.database_url.startswith("sqlite"):
            raise ValueError("Production requires PostgreSQL/PostGIS, not SQLite.")
        if not self.malware_scan_required:
            raise ValueError("Production must enable fail-closed malware scanning.")
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor so the .env file is only parsed once."""
    return Settings()

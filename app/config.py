"""
Application configuration.

All settings are read from environment variables (via a .env file in local
development). This keeps the service configurable without code changes,
which matters for deployment on platforms like Render or Railway.
"""

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- General ---
    app_name: str = "OCR Microservice"
    log_level: str = "INFO"

    # --- File validation ---
    max_file_size_mb: int = 10
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
    llm_model_name: str = "llama3-70b-8192"


    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor so the .env file is only parsed once."""
    return Settings()

"""Application configuration module using Pydantic Settings."""

from typing import List, Optional, Union
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    APP_NAME: str = "ai-image-screening-api"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # CORS configuration: comma-separated string or JSON list
    CORS_ORIGINS: Union[List[str], str] = [
        "https://safemate-frontend.onrender.com",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4321",
        "http://127.0.0.1:4321",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "*"
    ]

    # Still Image Model configuration
    MODEL_ID: str = "umm-maybe/AI-image-detector"
    HF_TOKEN: Optional[str] = None
    HF_API_URL: str = "https://api-inference.huggingface.co/models/umm-maybe/AI-image-detector"
    HF_HOME: Optional[str] = None
    TRANSFORMERS_CACHE: Optional[str] = None
    PRELOAD_MODELS_ON_STARTUP: bool = False

    # Video Deepfake Model configuration
    VIDEO_DEEPFAKE_MODEL_ID: str = "KoreaPeter/ms-eff-gcvit-deepfake-b0-ff-plus-plus"
    MAX_VIDEO_UPLOAD_MB: int = 50
    MAX_VIDEO_FRAMES_SAMPLED: int = 20
    MAX_VIDEO_DURATION_SECONDS: int = 180
    MAX_VIDEO_RESOLUTION_DIMENSION: int = 3840
    ALLOWED_VIDEO_EXTENSIONS: List[str] = [".mp4", ".mov", ".avi", ".webm", ".mkv"]
    ALLOWED_VIDEO_MIME_TYPES: List[str] = [
        "video/mp4",
        "video/quicktime",
        "video/x-msvideo",
        "video/webm",
        "video/x-matroska",
        "application/octet-stream",
    ]

    # Upload & processing constraints
    MAX_UPLOAD_MB: int = 10
    MAX_IMAGE_WIDTH: int = 4096
    MAX_IMAGE_HEIGHT: int = 4096
    MAX_IMAGE_PIXELS: int = 16_000_000  # Decompression bomb safeguard
    ALLOWED_IMAGE_FORMATS: List[str] = ["JPEG", "PNG", "WEBP"]
    ALLOWED_MIME_TYPES: List[str] = ["image/jpeg", "image/png", "image/webp"]

    LOG_LEVEL: str = "INFO"

    # Optional Groq API LLM Layer
    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL: str = "openai/gpt-oss-20b"
    GROQ_TIMEOUT_SECONDS: float = 8.0

    # Optional Google Gemini API LLM Layer (Fallback or Primary)
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-1.5-flash"
    GEMINI_TIMEOUT_SECONDS: float = 8.0

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str):
            if v.strip().startswith("["):
                # JSON array format handled by pydantic automatically
                import json
                try:
                    return json.loads(v)
                except Exception:
                    pass
            # Split comma-separated list
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_MB * 1024 * 1024

    @property
    def max_video_upload_bytes(self) -> int:
        return self.MAX_VIDEO_UPLOAD_MB * 1024 * 1024

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()

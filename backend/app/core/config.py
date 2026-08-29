from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # --- Security & Authentication ---
    ADMIN_PASSWORD: str = "admin123"
    SECRET_KEY: str = "ThisIsMyLongSecretKeyForJWT"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    # --- AWS ---
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    S3_BUCKET_NAME: str = ""
    REKOGNITION_FACE_MATCH_THRESHOLD: float = 80.0  # Rekognition uses 0–100 scale

    # --- Database & Storage Paths (relative to backend/ directory) ---
    DB_PATH: str = "data/app.db"
    UPLOAD_ROOT: str = "data/uploads/originals"
    THUMBNAIL_ROOT: str = "data/thumbnails"
    GUEST_SELFIES_DIR: str = "data/uploads/selfies"

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings():
    return Settings()

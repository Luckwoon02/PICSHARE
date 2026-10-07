from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # --- Security & Authentication ---
    SECRET_KEY: str = "ThisIsMyLongSecretKeyForJWT"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    # --- AWS ---
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    S3_BUCKET_NAME: str = ""
    REKOGNITION_FACE_MATCH_THRESHOLD: float = 80.0  # Rekognition uses 0–100 scale
    # Max IndexFaces calls per second. AWS's default quota is 50 in us-east-1, us-west-2 and
    # eu-west-1 and 5 in every other region, so 5 is safe everywhere. Raise it only if your
    # region's quota is higher.
    REKOGNITION_MAX_TPS: float = 5.0

    # --- Photo processing queue ---
    # Photos processed at once. Each one decodes a full-size image, so keep this low on a laptop.
    PHOTO_WORKER_CONCURRENCY: int = 4

    # --- Payments ---
    # While PAYMENT_REQUIRED is False, event owners may skip checkout (dev/testing).
    # Set it to True in production so an event only activates after a paid checkout.
    PAYMENT_REQUIRED: bool = False
    # "mock" simulates a checkout; a real gateway (e.g. Stripe) plugs in via
    # app/services/payment_service.py.
    PAYMENT_PROVIDER: str = "mock"
    PRICE_PER_GB_CENTS: int = 100
    # Flat per-event add-ons on top of the storage price, charged when the event's plan includes
    # the feature. 0 = included in the storage price (no extra charge).
    PRICE_FACE_SCAN_CENTS: int = 0
    PRICE_SELECTION_CENTS: int = 0
    PAYMENT_CURRENCY: str = "usd"
    MIN_STORAGE_GB: float = 1
    MAX_STORAGE_GB: float = 1000

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

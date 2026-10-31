from pydantic import field_validator
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # --- Security & Authentication ---
    # No default on purpose: the app won't start without one. Generate with:
    #   python -c "import secrets; print(secrets.token_hex(32))"
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    # --- Email (verification and password reset) ---
    # Where the links in those emails point.
    FRONTEND_URL: str = "http://localhost:3000"
    # Empty SMTP_HOST = no mail server: emails are printed to the backend console instead, so
    # you can copy the link while developing. Set it in production (any SMTP service, e.g. SES).
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_SSL: bool = False  # True = TLS from the start (usually port 465); False = STARTTLS
    EMAIL_FROM: str = "Pixello <no-reply@localhost>"
    EMAIL_VERIFY_EXPIRE_HOURS: int = 24
    PASSWORD_RESET_EXPIRE_MINUTES: int = 60

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

    # --- Live mode (photos pushed over FTP/SFTP into s3://bucket/live/{event_id}/) ---
    # SQS queue that S3 sends "new object" notifications to (see backend/setup_live_ingest.py).
    # Empty = no queue: the app finds new files by listing the bucket instead. Leave it empty on
    # laptops, so teammates sharing a bucket can't take each other's messages.
    INGEST_QUEUE_URL: str = ""
    # How often the app lists live events' folders in S3 when there is no queue...
    LIVE_POLL_SECONDS: int = 15
    # ...and how often it does so as a safety net when there is one.
    LIVE_SAFETY_SCAN_SECONDS: int = 300

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

    @field_validator("SECRET_KEY")
    @classmethod
    def _secret_key_is_long_enough(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        return v

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings():
    return Settings()

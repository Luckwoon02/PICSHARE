import hashlib
import hmac
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.services.db import db

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["auth"])

bearer_scheme = HTTPBearer(auto_error=False)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PBKDF2_ITERATIONS = 310_000

# Marks a token as a login token, so a selection or download token can never be used to log in.
TOKEN_TYPE = "access"

# Attempts allowed per window, in seconds.
WINDOW_SECONDS = 15 * 60
MAX_FAILED_LOGINS_PER_EMAIL = 5
MAX_FAILED_LOGINS_PER_IP = 30
MAX_REGISTRATIONS_PER_IP = 10

# Kept in memory, so it resets on restart and is per worker. Fine for one process; use Redis if
# the API ever runs on several.
_attempts: dict[str, list[float]] = {}


def _recent(key: str) -> list[float]:
    cutoff = time.monotonic() - WINDOW_SECONDS
    recent = [t for t in _attempts.get(key, []) if t > cutoff]
    if recent:
        _attempts[key] = recent
    else:
        _attempts.pop(key, None)
    return recent


def _check_limit(key: str, limit: int) -> None:
    if len(_recent(key)) >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Try again in a few minutes.",
        )


def _record(key: str) -> None:
    _attempts[key] = _recent(key) + [time.monotonic()]


def _client_ip(request: Request) -> str:
    # Behind a reverse proxy this is the proxy's address unless uvicorn runs with --proxy-headers.
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Password hashing (stdlib PBKDF2 — no extra native dependencies)
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    name: str
    email: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": user_id, "typ": TOKEN_TYPE, "exp": expire},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


def _auth_response(user: dict) -> AuthResponse:
    return AuthResponse(
        access_token=create_access_token(user["id"]),
        user=UserResponse(id=user["id"], name=user["name"], email=user["email"]),
    )


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> dict:
    """FastAPI dependency: resolves the logged-in user or raises 401."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if creds is None:
        raise credentials_exception
    try:
        payload = jwt.decode(
            creds.credentials, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        user_id = payload.get("sub")
    except JWTError:
        raise credentials_exception
    if payload.get("typ") != TOKEN_TYPE or not user_id:
        raise credentials_exception

    user = await db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user:
        raise credentials_exception
    return user


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(data: RegisterRequest, request: Request):
    ip_key = f"register:{_client_ip(request)}"
    _check_limit(ip_key, MAX_REGISTRATIONS_PER_IP)
    _record(ip_key)

    email = data.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Enter a valid email address")

    if await db.fetch_one("SELECT id FROM users WHERE email = ?", (email,)):
        raise HTTPException(status_code=409, detail="An account with this email already exists")

    user = {
        "id": str(uuid.uuid4()),
        "name": data.name.strip(),
        "email": email,
    }
    await db.execute(
        "INSERT INTO users (id, name, email, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
        (user["id"], user["name"], email, hash_password(data.password),
         datetime.utcnow().isoformat()),
    )
    return _auth_response(user)


@router.post("/login", response_model=AuthResponse)
async def login(data: LoginRequest, request: Request):
    email = data.email.strip().lower()
    email_key = f"login-email:{email}"
    ip_key = f"login-ip:{_client_ip(request)}"
    _check_limit(email_key, MAX_FAILED_LOGINS_PER_EMAIL)
    _check_limit(ip_key, MAX_FAILED_LOGINS_PER_IP)

    user = await db.fetch_one("SELECT * FROM users WHERE email = ?", (email,))
    if not user or not verify_password(data.password, user["password_hash"]):
        _record(email_key)
        _record(ip_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    _attempts.pop(email_key, None)
    return _auth_response(user)


@router.get("/me", response_model=UserResponse)
async def me(user: dict = Depends(get_current_user)):
    return UserResponse(id=user["id"], name=user["name"], email=user["email"])

import asyncio
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.services.db import db
from app.services.email_service import send_email

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
MAX_EMAILS_PER_ADDRESS = 3  # forgot-password / resend-verification, counted whether or not the account exists
MAX_EMAIL_REQUESTS_PER_IP = 10
MAX_BAD_TOKENS_PER_IP = 20

VERIFY_EMAIL = "verify_email"
RESET_PASSWORD = "reset_password"

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


# Checked against when the email has no account, so a login for an unknown email takes as long
# as one for a known email with the wrong password.
_DUMMY_HASH = hash_password(secrets.token_hex(8))


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


class MessageResponse(BaseModel):
    message: str


class EmailRequest(BaseModel):
    email: str


class TokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=128)


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

def create_access_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": user_id, "typ": TOKEN_TYPE, "iat": int(now.timestamp()), "exp": expire},
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
    # A password reset signs out every session that was started before it.
    changed_at = user.get("password_changed_at")
    if changed_at and (payload.get("iat") or 0) < changed_at:
        raise credentials_exception
    return user


# ---------------------------------------------------------------------------
# Emailed single-use links
# ---------------------------------------------------------------------------

def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _issue_token(user_id: str, purpose: str, lifetime: timedelta) -> str:
    """Make a link token for the user, replacing any earlier one for the same purpose."""
    raw = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    await db.execute("DELETE FROM auth_tokens WHERE user_id = ? AND purpose = ?", (user_id, purpose))
    await db.execute(
        "INSERT INTO auth_tokens (id, user_id, purpose, token_hash, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), user_id, purpose, _hash_token(raw),
         (now + lifetime).isoformat(), now.isoformat()),
    )
    return raw


async def _consume_token(raw: str, purpose: str) -> str | None:
    """Use up a link token. Returns the user's id, or None if it is unknown, expired or already used."""
    row = await db.fetch_one(
        "SELECT * FROM auth_tokens WHERE token_hash = ? AND purpose = ?", (_hash_token(raw), purpose)
    )
    if not row:
        return None
    # Delete first and check it was ours to delete, so two requests can't both use one link.
    cursor = await db.connection.execute("DELETE FROM auth_tokens WHERE id = ?", (row["id"],))
    await db.connection.commit()
    if cursor.rowcount != 1:
        return None
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        return None
    return row["user_id"]


def _link(path: str, token: str) -> str:
    return f"{settings.FRONTEND_URL.rstrip('/')}{path}?token={token}"


async def _send_verification(email: str, name: str, token: str) -> None:
    await send_email(
        email,
        "Confirm your Pixello account",
        f"Hi {name},\n\nConfirm your email address to finish creating your Pixello account:\n\n"
        f"{_link('/verify-email', token)}\n\n"
        f"The link works once and expires in {settings.EMAIL_VERIFY_EXPIRE_HOURS} hours. "
        "If you didn't sign up, ignore this email.\n",
    )


async def _send_already_registered(email: str, name: str) -> None:
    base = settings.FRONTEND_URL.rstrip("/")
    await send_email(
        email,
        "You already have a Pixello account",
        f"Hi {name},\n\nSomeone tried to create a Pixello account with this email address, but you "
        f"already have one.\n\nLog in: {base}/login\nForgot your password? {base}/forgot-password\n\n"
        "If it wasn't you, ignore this email. Nothing has changed.\n",
    )


async def _send_reset(email: str, name: str, token: str) -> None:
    await send_email(
        email,
        "Reset your Pixello password",
        f"Hi {name},\n\nSomeone asked to reset the password for your Pixello account. Choose a new one here:\n\n"
        f"{_link('/reset-password', token)}\n\n"
        f"The link works once and expires in {settings.PASSWORD_RESET_EXPIRE_MINUTES} minutes. "
        "If it wasn't you, ignore this email. Your password stays as it is.\n",
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

CHECK_YOUR_EMAIL = MessageResponse(message="Check your email to finish creating your account.")
RESET_SENT = MessageResponse(message="If that email has an account, we've sent a link to reset the password.")
VERIFY_SENT = MessageResponse(message="If that account still needs confirming, we've sent a new link.")


@router.post("/register", response_model=MessageResponse, status_code=202)
async def register(data: RegisterRequest, request: Request, background: BackgroundTasks):
    """
    Answers the same whether or not the email already has an account, so it can't be used to find
    out who is registered. No login token comes back: the person proves the address is theirs by
    opening the link we email, and that is what verify-email turns into a login.
    """
    ip_key = f"register:{_client_ip(request)}"
    _check_limit(ip_key, MAX_REGISTRATIONS_PER_IP)
    _record(ip_key)

    email = data.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    name = data.name.strip()

    # Hashed even when the account exists, so both cases take about as long.
    password_hash = await asyncio.to_thread(hash_password, data.password)

    existing = await db.fetch_one("SELECT * FROM users WHERE email = ?", (email,))
    if existing and existing["email_verified_at"]:
        background.add_task(_send_already_registered, email, existing["name"])
        return CHECK_YOUR_EMAIL

    if existing:
        # Signed up before but never confirmed: the latest form wins, and the old link stops working.
        user_id = existing["id"]
        await db.execute(
            "UPDATE users SET name = ?, password_hash = ? WHERE id = ?", (name, password_hash, user_id)
        )
    else:
        user_id = str(uuid.uuid4())
        try:
            await db.execute(
                "INSERT INTO users (id, name, email, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, name, email, password_hash, datetime.utcnow().isoformat()),
            )
        except sqlite3.IntegrityError:
            return CHECK_YOUR_EMAIL  # a simultaneous sign-up with this email won the race

    token = await _issue_token(user_id, VERIFY_EMAIL, timedelta(hours=settings.EMAIL_VERIFY_EXPIRE_HOURS))
    background.add_task(_send_verification, email, name, token)
    return CHECK_YOUR_EMAIL


@router.post("/login", response_model=AuthResponse)
async def login(data: LoginRequest, request: Request):
    email = data.email.strip().lower()
    email_key = f"login-email:{email}"
    ip_key = f"login-ip:{_client_ip(request)}"
    _check_limit(email_key, MAX_FAILED_LOGINS_PER_EMAIL)
    _check_limit(ip_key, MAX_FAILED_LOGINS_PER_IP)

    user = await db.fetch_one("SELECT * FROM users WHERE email = ?", (email,))
    stored = user["password_hash"] if user else _DUMMY_HASH
    password_ok = await asyncio.to_thread(verify_password, data.password, stored)
    if not user or not password_ok:
        _record(email_key)
        _record(ip_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Only someone who knows the password learns the account is unconfirmed.
    if not user["email_verified_at"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Confirm your email address first. We sent you a link when you signed up.",
        )
    _attempts.pop(email_key, None)
    return _auth_response(user)


@router.post("/verify-email", response_model=AuthResponse)
async def verify_email(data: TokenRequest, request: Request):
    """Opening the link we emailed confirms the address, and signs the person in."""
    bad_key = f"bad-token:{_client_ip(request)}"
    _check_limit(bad_key, MAX_BAD_TOKENS_PER_IP)
    user_id = await _consume_token(data.token, VERIFY_EMAIL)
    if not user_id:
        _record(bad_key)
        raise HTTPException(status_code=400, detail="This link has expired or was already used.")
    await db.execute(
        "UPDATE users SET email_verified_at = COALESCE(email_verified_at, ?) WHERE id = ?",
        (datetime.utcnow().isoformat(), user_id),
    )
    user = await db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
    return _auth_response(user)


async def _email_request_limits(email: str, request: Request) -> None:
    """Throttles by address and by IP, whether or not the address has an account."""
    email_key = f"email-request:{email}"
    ip_key = f"email-request-ip:{_client_ip(request)}"
    _check_limit(email_key, MAX_EMAILS_PER_ADDRESS)
    _check_limit(ip_key, MAX_EMAIL_REQUESTS_PER_IP)
    _record(email_key)
    _record(ip_key)


@router.post("/resend-verification", response_model=MessageResponse, status_code=202)
async def resend_verification(data: EmailRequest, request: Request, background: BackgroundTasks):
    email = data.email.strip().lower()
    await _email_request_limits(email, request)
    user = await db.fetch_one("SELECT * FROM users WHERE email = ?", (email,))
    if user and not user["email_verified_at"]:
        token = await _issue_token(user["id"], VERIFY_EMAIL, timedelta(hours=settings.EMAIL_VERIFY_EXPIRE_HOURS))
        background.add_task(_send_verification, email, user["name"], token)
    return VERIFY_SENT


@router.post("/forgot-password", response_model=MessageResponse, status_code=202)
async def forgot_password(data: EmailRequest, request: Request, background: BackgroundTasks):
    email = data.email.strip().lower()
    await _email_request_limits(email, request)
    user = await db.fetch_one("SELECT * FROM users WHERE email = ?", (email,))
    if user:
        token = await _issue_token(user["id"], RESET_PASSWORD, timedelta(minutes=settings.PASSWORD_RESET_EXPIRE_MINUTES))
        background.add_task(_send_reset, email, user["name"], token)
    return RESET_SENT


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(data: ResetPasswordRequest, request: Request):
    bad_key = f"bad-token:{_client_ip(request)}"
    _check_limit(bad_key, MAX_BAD_TOKENS_PER_IP)
    user_id = await _consume_token(data.token, RESET_PASSWORD)
    if not user_id:
        _record(bad_key)
        raise HTTPException(status_code=400, detail="This link has expired or was already used.")
    password_hash = await asyncio.to_thread(hash_password, data.password)
    now = datetime.now(timezone.utc)
    # Getting the link proves the person controls the mailbox, so this also confirms the address.
    # password_changed_at signs out every session started before now.
    await db.execute(
        "UPDATE users SET password_hash = ?, password_changed_at = ?, "
        "email_verified_at = COALESCE(email_verified_at, ?) WHERE id = ?",
        (password_hash, int(now.timestamp()), now.replace(tzinfo=None).isoformat(), user_id),
    )
    await db.execute("DELETE FROM auth_tokens WHERE user_id = ?", (user_id,))
    return MessageResponse(message="Your password has been changed. Log in with the new one.")


@router.get("/me", response_model=UserResponse)
async def me(user: dict = Depends(get_current_user)):
    return UserResponse(id=user["id"], name=user["name"], email=user["email"])

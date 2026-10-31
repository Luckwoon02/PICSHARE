import asyncio
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt

from app.api.auth import (
    AuthResponse,
    MAX_BAD_TOKENS_PER_IP,
    TokenRequest,
    _auth_response,
    _check_limit,
    _client_ip,
    _consume_token,
    _issue_token,
    _record,
    hash_password,
)
from app.core.config import get_settings
from app.services.db import db

settings = get_settings()
router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

STATE_COOKIE = "oauth_state"
STATE_MINUTES = 10  # how long a person has to finish signing in at Google
OAUTH_LOGIN = "oauth_login"
LOGIN_CODE_SECONDS = 60  # the one-time code handed to the frontend


def _google_enabled() -> bool:
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def _require_google() -> None:
    if not _google_enabled():
        raise HTTPException(status_code=404, detail="Sign in with Google isn't set up")


def _frontend(path: str, **params: str) -> str:
    query = f"?{urlencode(params)}" if params else ""
    return f"{settings.FRONTEND_URL.rstrip('/')}{path}{query}"


def _callback_path() -> str:
    return urlparse(settings.GOOGLE_REDIRECT_URI).path


def _failed(reason: str) -> RedirectResponse:
    response = RedirectResponse(_frontend("/oauth/callback", error=reason), status_code=302)
    response.delete_cookie(STATE_COOKIE, path=_callback_path())
    return response


@router.get("/providers")
async def providers():
    """Which "continue with…" buttons the login page should show."""
    return {"google": _google_enabled()}


@router.get("/google/login")
async def google_login():
    """
    Sends the browser to Google. A random `state` (kept in a cookie as well) proves the person who
    comes back is the one who left, and the PKCE challenge stops a stolen `code` being used elsewhere.
    """
    _require_google()
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

    url = GOOGLE_AUTH_URL + "?" + urlencode({
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    })
    cookie = jwt.encode(
        {"typ": "oauth_state", "state": state, "verifier": verifier,
         "exp": datetime.now(timezone.utc) + timedelta(minutes=STATE_MINUTES)},
        settings.SECRET_KEY, algorithm=settings.ALGORITHM,
    )
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        STATE_COOKIE, cookie, max_age=STATE_MINUTES * 60, httponly=True, samesite="lax",
        secure=settings.GOOGLE_REDIRECT_URI.startswith("https://"), path=_callback_path(),
    )
    return response


def _fetch_google_profile(code: str, verifier: str) -> dict:
    """Trade the code for the person's profile. Blocking: run it in a thread."""
    token = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
        timeout=10,
    )
    token.raise_for_status()
    profile = requests.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {token.json()['access_token']}"},
        timeout=10,
    )
    profile.raise_for_status()
    return profile.json()


async def _user_for_google(profile: dict) -> dict:
    """The user this Google account signs in as: found by Google id, then by email, else created."""
    subject = str(profile["sub"])
    email = profile["email"].strip().lower()
    now = datetime.now(timezone.utc)

    identity = await db.fetch_one(
        "SELECT user_id FROM oauth_identities WHERE provider = 'google' AND subject = ?", (subject,)
    )
    user = await db.fetch_one("SELECT * FROM users WHERE id = ?", (identity["user_id"],)) if identity else None
    if user:
        return user

    user = await db.fetch_one("SELECT * FROM users WHERE email = ?", (email,))
    if user and not user["email_verified_at"]:
        # Someone signed up with this email by password but never confirmed it, and now the real
        # owner has proved the address through Google. Whoever chose that password may not be the
        # owner, so it is thrown away, along with any session it started.
        await db.execute(
            "UPDATE users SET password_hash = ?, password_changed_at = ?, email_verified_at = ? WHERE id = ?",
            (hash_password(secrets.token_hex(16)), int(now.timestamp()),
             now.replace(tzinfo=None).isoformat(), user["id"]),
        )
        user = await db.fetch_one("SELECT * FROM users WHERE id = ?", (user["id"],))
    elif not user:
        user = {
            "id": secrets.token_hex(16),
            "name": (profile.get("name") or email.split("@")[0]).strip()[:100],
            "email": email,
        }
        await db.execute(
            "INSERT INTO users (id, name, email, password_hash, created_at, email_verified_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            # Nobody knows this password. They can set one with "forgot password" if they want to.
            (user["id"], user["name"], email, hash_password(secrets.token_hex(16)),
             now.replace(tzinfo=None).isoformat(), now.replace(tzinfo=None).isoformat()),
        )

    await db.execute(
        "INSERT OR IGNORE INTO oauth_identities (provider, subject, user_id, created_at) VALUES ('google', ?, ?, ?)",
        (subject, user["id"], now.isoformat()),
    )
    return user


@router.get("/google/callback")
async def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    _require_google()
    if error:
        return _failed("cancelled" if error == "access_denied" else "failed")

    # The cookie from /google/login must say the same state that Google sent back.
    try:
        saved = jwt.decode(request.cookies.get(STATE_COOKIE, ""), settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return _failed("expired")
    if saved.get("typ") != "oauth_state" or not state or not hmac.compare_digest(state, saved.get("state", "")):
        return _failed("failed")
    if not code:
        return _failed("failed")

    try:
        profile = await asyncio.to_thread(_fetch_google_profile, code, saved["verifier"])
    except Exception as e:
        print(f"[oauth] Google sign-in failed: {type(e).__name__}: {e}", flush=True)
        return _failed("failed")

    # Google must vouch for the address: otherwise anyone could claim someone else's email.
    if not profile.get("sub") or not profile.get("email") or profile.get("email_verified") is not True:
        return _failed("unverified_email")

    user = await _user_for_google(profile)
    login_code = await _issue_token(user["id"], OAUTH_LOGIN, timedelta(seconds=LOGIN_CODE_SECONDS))
    response = RedirectResponse(_frontend("/oauth/callback", code=login_code), status_code=302)
    response.delete_cookie(STATE_COOKIE, path=_callback_path())
    return response


@router.post("/oauth/exchange", response_model=AuthResponse)
async def oauth_exchange(data: TokenRequest, request: Request):
    """The frontend trades the one-time code from the redirect for a login. The code works once."""
    bad_key = f"bad-token:{_client_ip(request)}"
    _check_limit(bad_key, MAX_BAD_TOKENS_PER_IP)
    user_id = await _consume_token(data.token, OAUTH_LOGIN)
    if not user_id:
        _record(bad_key)
        raise HTTPException(status_code=400, detail="This sign-in link has expired. Please try again.")
    user = await db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
    return _auth_response(user)

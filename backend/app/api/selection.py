"""
selection.py — a photographer's client picks the photos for an album.

How it works:
  * The photographer makes one selection link per client. A link has a long random `token` (the URL)
    and a short `code` (typed by the client). The photographer sends both.
  * Entering the code gives the client a short-lived session token. Everything else needs it.
  * The client can only see thumbnails and a mid-size preview, never the original file.
  * A pick is a row in `selections` (link, photo). Nothing is copied, so a selection costs no
    storage. The photographer sees the picks in the dashboard and can download them as a ZIP
    (streamed from S3, never held in memory) or as a list of file names.

Two routers: `owner_router` (/selection, photographer logged in) and `client_router` (/select, client).
"""
import asyncio
import hashlib
import hmac
import io
import re
import secrets
import time
import zipfile
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.core.config import get_settings
from app.services.db import db
from app.services.event_access import get_owned_active_event
from app.services.s3_service import s3_service

settings = get_settings()
owner_router = APIRouter(prefix="/selection", tags=["selection"])
client_router = APIRouter(prefix="/select", tags=["selection"])
bearer = HTTPBearer(auto_error=False)

# No 0/O or 1/I/L: the client types this on a phone
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LENGTH = 6
SESSION_HOURS = 8
IMAGE_URL_SECONDS = 12 * 3600  # signed image links; see S3Service.get_presigned_url for the reuse window
MAX_WRONG_CODES = 5
LOCKOUT_SECONDS = 300
PAGE_SIZE = 60

# token -> [wrong guesses, locked until (monotonic seconds)]. In memory: the API runs as one process.
_guesses: dict[str, list[float]] = {}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.utcnow().isoformat()


def _code_version(link: dict) -> str:
    """Changes whenever the code does, so a new code ends every session opened with the old one."""
    return hashlib.sha256(f"{link['code']}:{link['token']}".encode()).hexdigest()[:12]


def _require_selection(event: dict) -> None:
    if not event.get("selection_enabled"):
        raise HTTPException(status_code=403, detail="Photo selection isn't part of this event's plan")


def _link_view(link: dict) -> dict:
    return {
        "id": link["id"],
        "event_id": link["event_id"],
        "client_name": link["client_name"],
        "token": link["token"],
        "code": link["code"],
        "path": f"/select/{link['token']}",
        "status": link["status"],
        "submitted_at": link.get("submitted_at"),
        "created_at": link["created_at"],
        "selected_count": link.get("selected_count", 0),
    }


_COUNT_SQL = (
    "(SELECT COUNT(*) FROM selections s JOIN photos p ON p.id = s.photo_id WHERE s.link_id = l.id)"
)


async def _owned_link(link_id: str, user: dict) -> dict:
    """The link, if it belongs to one of this photographer's events; 404 otherwise."""
    row = await db.fetch_one(
        f"""
        SELECT l.*, {_COUNT_SQL} AS selected_count FROM selection_links l
        JOIN events e ON e.id = l.event_id
        WHERE l.id = ? AND e.owner_id = ?
        """,
        (link_id, user["id"]),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Selection link not found")
    return row


async def _photo_entries(rows: list[dict], selected: set[str] | None) -> list[dict]:
    """Thumbnail + preview links for photos. Never includes the original file."""
    async def _url(key: str | None) -> str | None:
        if not key:
            return None
        try:
            return await s3_service.get_presigned_url(key, expiry_seconds=IMAGE_URL_SECONDS)
        except Exception as e:
            print(f"[selection] could not sign {key}: {type(e).__name__}: {e}")
            return None

    thumbs, previews = await asyncio.gather(
        asyncio.gather(*(_url(r["thumbnail_s3_key"]) for r in rows)),
        # Photos uploaded before previews existed fall back to the thumbnail
        asyncio.gather(*(_url(r.get("preview_s3_key") or r["thumbnail_s3_key"]) for r in rows)),
    )
    entries = []
    for r, thumb, preview in zip(rows, thumbs, previews):
        entry = {
            "id": r["id"],
            "filename": r["original_file_name"],
            "width": r["width"],
            "height": r["height"],
            "thumbnail_url": thumb,
            "preview_url": preview,
        }
        if selected is not None:
            entry["selected"] = r["id"] in selected
        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# Photographer: manage links and see what the client picked
# ---------------------------------------------------------------------------

class LinkCreate(BaseModel):
    event_id: str
    client_name: str = Field(min_length=1, max_length=100)


class LinkUpdate(BaseModel):
    client_name: str | None = Field(default=None, min_length=1, max_length=100)
    reopen: bool = False  # let the client change a submitted selection
    new_code: bool = False  # also signs out anyone using the old code


@owner_router.post("/links")
async def create_link(data: LinkCreate, user: dict = Depends(get_current_user)):
    event = await get_owned_active_event(data.event_id, user)
    _require_selection(event)
    link = {
        "id": secrets.token_hex(16),
        "event_id": data.event_id,
        "token": secrets.token_urlsafe(16),
        "code": "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH)),
        "client_name": data.client_name.strip(),
        "status": "open",
        "submitted_at": None,
        "created_at": _now(),
    }
    await db.execute(
        "INSERT INTO selection_links (id, event_id, token, code, client_name, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (link["id"], link["event_id"], link["token"], link["code"], link["client_name"], "open", link["created_at"]),
    )
    return _link_view(link)


@owner_router.get("/links")
async def list_links(event_id: str, user: dict = Depends(get_current_user)):
    await get_owned_active_event(event_id, user)
    rows = await db.fetch_all(
        f"""
        SELECT l.*, {_COUNT_SQL} AS selected_count FROM selection_links l
        WHERE l.event_id = ? ORDER BY l.created_at DESC
        """,
        (event_id,),
    )
    return {"links": [_link_view(r) for r in rows]}


@owner_router.patch("/links/{link_id}")
async def update_link(link_id: str, data: LinkUpdate, user: dict = Depends(get_current_user)):
    link = await _owned_link(link_id, user)
    if data.client_name:
        link["client_name"] = data.client_name.strip()
    if data.reopen:
        link["status"], link["submitted_at"] = "open", None
    if data.new_code:
        link["code"] = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        _guesses.pop(link["token"], None)
    await db.execute(
        "UPDATE selection_links SET client_name = ?, status = ?, submitted_at = ?, code = ? WHERE id = ?",
        (link["client_name"], link["status"], link["submitted_at"], link["code"], link_id),
    )
    return _link_view(link)


@owner_router.delete("/links/{link_id}")
async def delete_link(link_id: str, user: dict = Depends(get_current_user)):
    await _owned_link(link_id, user)
    await db.execute("DELETE FROM selections WHERE link_id = ?", (link_id,))
    await db.execute("DELETE FROM selection_links WHERE id = ?", (link_id,))
    return {"message": "Selection link deleted"}


_PICKED_SQL = """
    SELECT p.id, p.original_file_name, p.width, p.height, p.thumbnail_s3_key, p.preview_s3_key, p.s3_object_key
    FROM selections s JOIN photos p ON p.id = s.photo_id
    WHERE s.link_id = ?
    ORDER BY p.original_file_name COLLATE NOCASE, p.id
"""


@owner_router.get("/links/{link_id}/photos")
async def picked_photos(link_id: str, page: int = Query(1, ge=1), limit: int = Query(120, ge=1, le=500),
                        user: dict = Depends(get_current_user)):
    """The photos this client picked (thumbnails and names), for the dashboard."""
    link = await _owned_link(link_id, user)
    rows = await db.fetch_all(_PICKED_SQL + " LIMIT ? OFFSET ?", (link_id, limit, (page - 1) * limit))
    return {
        "photos": await _photo_entries(rows, None),
        "total": link["selected_count"],
        "page": page,
        "has_more": page * limit < link["selected_count"],
    }


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-") or "selection"


@owner_router.get("/links/{link_id}/filenames")
async def picked_filenames(link_id: str, user: dict = Depends(get_current_user)):
    """One original file name per line: handy for finding the same files in an editing tool."""
    link = await _owned_link(link_id, user)
    rows = await db.fetch_all(_PICKED_SQL, (link_id,))
    names = list(dict.fromkeys(r["original_file_name"] for r in rows if r["original_file_name"]))
    return PlainTextResponse(
        "\n".join(names) + "\n",
        headers={"Content-Disposition": f'attachment; filename="{_safe_name(link["client_name"])}-selection.txt"'},
    )


@owner_router.post("/links/{link_id}/download-url")
async def download_url(link_id: str, user: dict = Depends(get_current_user)):
    """
    A browser download can't send the login header, so hand out a short-lived address that carries
    its own proof of who asked. Good for a few minutes only.
    """
    await _owned_link(link_id, user)
    proof = jwt.encode(
        {"typ": "zip", "lid": link_id, "exp": datetime.utcnow() + timedelta(minutes=5)},
        settings.SECRET_KEY, algorithm=settings.ALGORITHM,
    )
    return {"url": f"/selection/download/{proof}"}


class _Sink(io.RawIOBase):
    """Where the ZIP writer puts its bytes: collected here, then handed on to the download."""

    def __init__(self):
        self._chunks: list[bytes] = []

    def writable(self):
        return True

    def write(self, data):
        self._chunks.append(bytes(data))
        return len(data)

    def drain(self) -> bytes:
        data = b"".join(self._chunks)
        self._chunks.clear()
        return data


def _zip_stream(photos: list[dict]):
    """
    Build the ZIP while it is being downloaded: each original streams from S3 straight into the
    response, so memory use stays at a few MB however big the album is. Files are stored without
    compression because JPEGs don't shrink, which also keeps the server's CPU idle.
    """
    sink = _Sink()
    used: set[str] = set()
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED) as archive:
        for photo in photos:
            name = photo["original_file_name"] or f"{photo['id']}.jpg"
            if name in used:  # two photos with the same file name
                stem, dot, ext = name.rpartition(".")
                n = 2
                while (candidate := f"{stem or name}_{n}{dot}{ext if dot else ''}") in used:
                    n += 1
                name = candidate
            used.add(name)
            try:
                body = s3_service.open_object(photo["s3_object_key"])
            except Exception as e:
                print(f"[selection] zip: skipping {name}: {type(e).__name__}: {e}")
                continue
            info = zipfile.ZipInfo(name, date_time=time.localtime()[:6])
            info.compress_type = zipfile.ZIP_STORED
            try:
                with archive.open(info, "w", force_zip64=True) as out:
                    for chunk in body.iter_chunks(1024 * 1024):
                        out.write(chunk)
                        data = sink.drain()
                        if data:
                            yield data
            finally:
                body.close()
    tail = sink.drain()  # the ZIP's table of contents is written when the archive closes
    if tail:
        yield tail


@owner_router.get("/download/{proof}")
async def download_zip(proof: str):
    try:
        payload = jwt.decode(proof, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="This download link has expired. Start the download again.")
    if payload.get("typ") != "zip":
        raise HTTPException(status_code=401, detail="Invalid download link")
    link = await db.fetch_one("SELECT * FROM selection_links WHERE id = ?", (payload.get("lid"),))
    if not link:
        raise HTTPException(status_code=404, detail="Selection link not found")
    photos = await db.fetch_all(_PICKED_SQL, (link["id"],))
    photos = [p for p in photos if p["s3_object_key"]]
    if not photos:
        raise HTTPException(status_code=404, detail="Nothing has been selected yet")
    return StreamingResponse(
        _zip_stream(photos),  # a plain generator: Starlette runs it in a worker thread
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{_safe_name(link["client_name"])}-selection.zip"'},
    )


# ---------------------------------------------------------------------------
# Client: open the link, enter the code, look at photos, pick, submit
# ---------------------------------------------------------------------------

async def _link_by_token(token: str) -> dict:
    link = await db.fetch_one("SELECT * FROM selection_links WHERE token = ?", (token,))
    if not link:
        raise HTTPException(status_code=404, detail="This link isn't valid")
    return link


async def _event_for(link: dict) -> dict:
    event = await db.fetch_one("SELECT * FROM events WHERE id = ?", (link["event_id"],))
    if not event or event["status"] != "active" or not event.get("selection_enabled"):
        raise HTTPException(status_code=404, detail="This link isn't available")
    return event


class _Session(dict):
    pass


async def client_session(token: str, creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> _Session:
    """The client's link, once they have entered the right code (401 sends them back to the code screen)."""
    link = await _link_by_token(token)
    unauthorized = HTTPException(status_code=401, detail="Please enter your code again")
    if creds is None:
        raise unauthorized
    try:
        payload = jwt.decode(creds.credentials, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        raise unauthorized
    if payload.get("typ") != "select" or payload.get("lid") != link["id"] or payload.get("cv") != _code_version(link):
        raise unauthorized
    return _Session(link=link, event=await _event_for(link))


class CodeCheck(BaseModel):
    code: str = Field(min_length=1, max_length=20)


@client_router.get("/{token}")
async def link_info(token: str):
    """What the code screen shows. Nothing about the photos."""
    link = await _link_by_token(token)
    event = await _event_for(link)
    return {"client_name": link["client_name"], "event_name": event["name"], "status": link["status"]}


@client_router.post("/{token}/verify")
async def verify_code(token: str, data: CodeCheck):
    link = await _link_by_token(token)
    event = await _event_for(link)

    state = _guesses.setdefault(token, [0, 0.0])
    wait = state[1] - time.monotonic()
    if wait > 0:
        raise HTTPException(status_code=429, detail=f"Too many wrong codes. Try again in {int(wait // 60) + 1} minute(s).")

    typed = re.sub(r"\s+", "", data.code).upper()
    if not hmac.compare_digest(typed, link["code"]):
        state[0] += 1
        if state[0] >= MAX_WRONG_CODES:
            state[0], state[1] = 0, time.monotonic() + LOCKOUT_SECONDS
            raise HTTPException(status_code=429, detail="Too many wrong codes. Try again in 5 minutes.")
        raise HTTPException(status_code=401, detail="That code isn't right")

    _guesses.pop(token, None)
    session = jwt.encode(
        {"typ": "select", "lid": link["id"], "cv": _code_version(link),
         "exp": datetime.utcnow() + timedelta(hours=SESSION_HOURS)},
        settings.SECRET_KEY, algorithm=settings.ALGORITHM,
    )
    return {
        "session": session,
        "client_name": link["client_name"],
        "event_name": event["name"],
        "status": link["status"],
    }


async def _selected_total(link_id: str) -> int:
    return (await db.fetch_one(
        "SELECT COUNT(*) AS n FROM selections s JOIN photos p ON p.id = s.photo_id WHERE s.link_id = ?",
        (link_id,),
    ))["n"]


@client_router.get("/{token}/photos")
async def client_photos(
    token: str,
    page: int = Query(1, ge=1),
    only_selected: bool = False,
    session: _Session = Depends(client_session),
):
    """A page of the event's finished photos, with which ones this client has picked."""
    link, event = session["link"], session["event"]
    where = "p.event_id = ? AND p.status = 'processed' AND p.thumbnail_s3_key IS NOT NULL"
    params: list = [event["id"]]
    if only_selected:
        where += " AND p.id IN (SELECT photo_id FROM selections WHERE link_id = ?)"
        params.append(link["id"])

    total = (await db.fetch_one(f"SELECT COUNT(*) AS n FROM photos p WHERE {where}", params))["n"]
    rows = await db.fetch_all(
        f"""
        SELECT p.id, p.original_file_name, p.width, p.height, p.thumbnail_s3_key, p.preview_s3_key
        FROM photos p WHERE {where}
        ORDER BY p.original_file_name COLLATE NOCASE, p.id LIMIT ? OFFSET ?
        """,
        [*params, PAGE_SIZE, (page - 1) * PAGE_SIZE],
    )
    picked: set[str] = set()
    if rows:
        marks = ",".join("?" * len(rows))
        picked = {r["photo_id"] for r in await db.fetch_all(
            f"SELECT photo_id FROM selections WHERE link_id = ? AND photo_id IN ({marks})",
            [link["id"], *[r["id"] for r in rows]],
        )}
    return {
        "photos": await _photo_entries(rows, picked),
        "total": total,
        "page": page,
        "has_more": page * PAGE_SIZE < total,
        "selected_total": await _selected_total(link["id"]),
        "status": link["status"],
    }


class SelectionChange(BaseModel):
    add: list[str] = Field(default_factory=list, max_length=500)
    remove: list[str] = Field(default_factory=list, max_length=500)


@client_router.post("/{token}/selection")
async def change_selection(token: str, data: SelectionChange, session: _Session = Depends(client_session)):
    """Add and remove picks in one go (the page batches taps to save requests)."""
    link, event = session["link"], session["event"]
    if link["status"] == "submitted":
        raise HTTPException(status_code=409, detail="This selection was already sent to your photographer")

    if data.add:
        marks = ",".join("?" * len(data.add))
        valid = [r["id"] for r in await db.fetch_all(
            f"SELECT id FROM photos WHERE event_id = ? AND status = 'processed' AND id IN ({marks})",
            [event["id"], *data.add],
        )]
        await db.executemany(
            "INSERT OR IGNORE INTO selections (link_id, photo_id, created_at) VALUES (?, ?, ?)",
            [(link["id"], photo_id, _now()) for photo_id in valid],
        )
    if data.remove:
        await db.executemany(
            "DELETE FROM selections WHERE link_id = ? AND photo_id = ?",
            [(link["id"], photo_id) for photo_id in data.remove],
        )
    return {"selected_total": await _selected_total(link["id"])}


@client_router.post("/{token}/submit")
async def submit_selection(token: str, session: _Session = Depends(client_session)):
    link = session["link"]
    total = await _selected_total(link["id"])
    if total == 0:
        raise HTTPException(status_code=400, detail="Pick at least one photo first")
    if link["status"] != "submitted":
        await db.execute(
            "UPDATE selection_links SET status = 'submitted', submitted_at = ? WHERE id = ?", (_now(), link["id"])
        )
    return {"status": "submitted", "selected_total": total}

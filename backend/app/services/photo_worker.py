"""
photo_worker.py — durable background processing for uploaded photos.

The `photos` table is the queue. A photo with status='pending' and an S3 key is a
job waiting to run. A background loop claims jobs, runs the pipeline
(download -> dimensions -> thumbnail -> face indexing) and records the result.

Because the queue lives in the database, nothing is lost if the server restarts:
unfinished jobs are simply picked up again. Failed jobs retry with backoff and,
once MAX_ATTEMPTS is used up, are marked 'error' so the dashboard's Retry button
can offer them again.

Columns used: status, attempts, claimed_at, next_attempt_at, error_detail.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta

from PIL import Image

from app.core.config import get_settings
from app.services.db import db
from app.services.rekognition_service import rekognition_service
from app.services.s3_service import s3_service
from app.services.thumbnail_service import thumbnail_service

settings = get_settings()

CONCURRENCY = 8  # photos processed at once
MAX_ATTEMPTS = 4  # per photo, across restarts and retries
BACKOFF_BASE_SECONDS = 30  # wait before attempt n+1 is BASE * 2**(n-1)
STALE_CLAIM = timedelta(minutes=10)  # a claim older than this is a crashed/hung job
POLL_SECONDS = 5  # how often to look for due jobs when nothing wakes us

STEP_RETRY_ATTEMPTS = 3  # quick in-process retries around each AWS call
STEP_RETRY_BASE_DELAY = 1.0

os.makedirs(settings.UPLOAD_ROOT, exist_ok=True)
os.makedirs(settings.THUMBNAIL_ROOT, exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers shared with the API routes
# ---------------------------------------------------------------------------

def s3_original_key(event_id: str, photo_id: str, ext: str) -> str:
    return f"events/{event_id}/originals/{photo_id}.{ext}"


def s3_thumbnail_key(event_id: str, photo_id: str) -> str:
    return f"events/{event_id}/thumbnails/{photo_id}.jpg"


def remove_silently(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print(f"[cleanup] Could not remove {path}: {e}")


async def with_retry(make_call, label: str):
    """
    Await make_call() up to STEP_RETRY_ATTEMPTS times with exponential backoff.
    make_call is a zero-arg callable returning a fresh coroutine each time.
    Re-raises the last error if every attempt fails.
    """
    for attempt in range(1, STEP_RETRY_ATTEMPTS + 1):
        try:
            return await make_call()
        except Exception as e:
            if attempt == STEP_RETRY_ATTEMPTS:
                raise
            delay = STEP_RETRY_BASE_DELAY * 2 ** (attempt - 1)
            print(f"[retry] {label} failed ({type(e).__name__}: {e}); attempt {attempt}/{STEP_RETRY_ATTEMPTS}, retrying in {delay:.0f}s")
            await asyncio.sleep(delay)


def _now() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# Queue loop
# ---------------------------------------------------------------------------

_wake: asyncio.Event | None = None
_task: asyncio.Task | None = None


def notify() -> None:
    """Tell the worker there is new work, so it doesn't wait for the next poll."""
    if _wake is not None:
        _wake.set()


async def start() -> None:
    """Start the worker loop. Call once at app startup, after the DB is connected."""
    global _wake, _task
    if _task is not None:
        return
    _wake = asyncio.Event()

    # Single API process: anything still marked as claimed was cut off by the restart.
    await db.execute("UPDATE photos SET claimed_at = NULL WHERE status = 'pending'")
    _task = asyncio.create_task(_run(), name="photo-worker")
    print("[worker] started")


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None


async def _give_up_exhausted() -> None:
    """Jobs that used every attempt but never reported back (crash, hang) become errors."""
    await db.execute(
        """
        UPDATE photos SET status = 'error', claimed_at = NULL,
            error_detail = COALESCE(error_detail, 'Processing was interrupted repeatedly')
        WHERE status = 'pending' AND attempts >= ?
          AND (claimed_at IS NULL OR claimed_at < ?)
        """,
        (MAX_ATTEMPTS, (_now() - STALE_CLAIM).isoformat()),
    )


async def _claim(limit: int) -> list[dict]:
    now = _now()
    rows = await db.fetch_all(
        """
        SELECT id, event_id, original_file_name, s3_object_key, thumbnail_s3_key, attempts
        FROM photos
        WHERE status = 'pending' AND s3_object_key IS NOT NULL AND attempts < ?
          AND (claimed_at IS NULL OR claimed_at < ?)
          AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
        ORDER BY created_at
        LIMIT ?
        """,
        (MAX_ATTEMPTS, (now - STALE_CLAIM).isoformat(), now.isoformat(), limit),
    )
    for row in rows:  # single worker loop, so no other claimer can interleave
        await db.execute(
            "UPDATE photos SET claimed_at = ?, attempts = attempts + 1 WHERE id = ?",
            (now.isoformat(), row["id"]),
        )
        row["attempts"] += 1
    return rows


async def _run() -> None:
    running: set[asyncio.Task] = set()

    def _done(task: asyncio.Task) -> None:
        running.discard(task)
        _wake.set()  # a slot freed up: look for more work right away

    while True:
        try:
            _wake.clear()
            await _give_up_exhausted()
            free = CONCURRENCY - len(running)
            if free > 0:
                for photo in await _claim(free):
                    task = asyncio.create_task(_process_safely(photo))
                    running.add(task)
                    task.add_done_callback(_done)
        except asyncio.CancelledError:
            for task in running:
                task.cancel()
            raise
        except Exception as e:
            print(f"[worker] loop error: {type(e).__name__}: {e}")

        try:
            await asyncio.wait_for(_wake.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            pass


# ---------------------------------------------------------------------------
# One job
# ---------------------------------------------------------------------------

class _PermanentError(Exception):
    """Retrying won't help (e.g. the file isn't a readable image)."""


async def _process_safely(photo: dict) -> None:
    try:
        await _process(photo)
    except asyncio.CancelledError:
        raise  # shutting down; the claim is released on next start
    except Exception as e:
        print(f"[worker] unexpected error for {photo['id']}: {type(e).__name__}: {e}")
        await _fail(photo, f"Unexpected error: {type(e).__name__}: {e}")


async def _fail(photo: dict, reason: str, permanent: bool = False) -> None:
    """Requeue with backoff, or mark as error once attempts are used up."""
    reason = reason[:500]
    if permanent or photo["attempts"] >= MAX_ATTEMPTS:
        await db.execute(
            "UPDATE photos SET status = 'error', claimed_at = NULL, error_detail = ? WHERE id = ?",
            (reason, photo["id"]),
        )
        print(f"[worker] {photo['id']} failed for good: {reason}")
        return
    delay = BACKOFF_BASE_SECONDS * 2 ** (photo["attempts"] - 1)
    await db.execute(
        "UPDATE photos SET claimed_at = NULL, next_attempt_at = ?, error_detail = ? WHERE id = ?",
        ((_now() + timedelta(seconds=delay)).isoformat(), reason, photo["id"]),
    )
    print(f"[worker] {photo['id']} attempt {photo['attempts']}/{MAX_ATTEMPTS} failed ({reason}); retry in {delay}s")


async def _process(photo: dict) -> None:
    photo_id, event_id, s3_key = photo["id"], photo["event_id"], photo["s3_object_key"]
    ext = os.path.splitext(s3_key)[1].lstrip(".") or "jpg"
    local = os.path.join(settings.UPLOAD_ROOT, f"work-{photo_id}.{ext}")
    thumb_local = os.path.join(settings.THUMBNAIL_ROOT, f"{photo_id}.jpg")
    try:
        # 1. Fetch the original that the browser uploaded to S3
        try:
            await with_retry(lambda: s3_service.download_file(s3_key, local), f"S3 download {photo_id}")
        except Exception as e:
            return await _fail(photo, f"Could not read original from S3: {type(e).__name__}: {e}")

        # 2. Dimensions (a file that isn't a valid image will never succeed)
        def _size(path: str):
            with Image.open(path) as img:
                return img.size
        try:
            width, height = await asyncio.to_thread(_size, local)
        except Exception as e:
            return await _fail(photo, f"Image read failed: {type(e).__name__}: {e}", permanent=True)

        # 3. Thumbnail (non-fatal; skipped if an earlier attempt already made it)
        thumb_key = photo.get("thumbnail_s3_key")
        if not thumb_key:
            candidate = s3_thumbnail_key(event_id, photo_id)
            try:
                await asyncio.to_thread(thumbnail_service.generate_thumbnail, local, thumb_local)
                await with_retry(
                    lambda: s3_service.upload_file(thumb_local, candidate, "image/jpeg"),
                    f"S3 thumbnail {photo_id}",
                )
                thumb_key = candidate
            except Exception as e:
                print(f"[worker] thumbnail failed for {photo_id}: {type(e).__name__}: {e}")
        await db.execute(
            "UPDATE photos SET width = ?, height = ?, thumbnail_s3_key = ? WHERE id = ?",
            (width, height, thumb_key, photo_id),
        )

        # 4. Face indexing — skipped if faces are already stored, so a retry never duplicates them
        faces_count = (await db.fetch_one(
            "SELECT COUNT(*) AS n FROM faces WHERE photo_id = ?", (photo_id,)
        ))["n"]
        warning = None
        if faces_count == 0:
            try:
                faces = await with_retry(
                    lambda: rekognition_service.index_photo_faces(s3_key, photo_id, event_id),
                    f"Rekognition {photo_id}",
                )
                now = _now().isoformat()
                for face in faces:
                    await db.execute(
                        "INSERT INTO faces (id, photo_id, event_id, rekognition_face_id, created_at) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (str(uuid.uuid4()), photo_id, event_id, face["rekognition_face_id"], now),
                    )
                faces_count = len(faces)
            except Exception as e:
                warning = f"Rekognition failed: {type(e).__name__}: {e}"

        if warning and photo["attempts"] < MAX_ATTEMPTS:
            return await _fail(photo, warning)  # try the whole job again later

        # 5. Done. A last-attempt Rekognition failure still counts as processed, with the
        #    warning kept so the dashboard's Retry button can offer it again.
        await db.execute(
            """
            UPDATE photos SET status = 'processed', claimed_at = NULL, next_attempt_at = NULL,
                faces_count = ?, error_detail = ?
            WHERE id = ?
            """,
            (faces_count, warning[:500] if warning else None, photo_id),
        )
        print(f"[worker] done {photo_id} | {faces_count} face(s) | {width}x{height}")
    finally:
        remove_silently(local)
        remove_silently(thumb_local)

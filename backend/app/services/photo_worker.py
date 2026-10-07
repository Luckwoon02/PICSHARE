"""
photo_worker.py — durable background processing for uploaded photos.

The `photos` table is the queue. A photo with status='pending' and an S3 key is a
job waiting to run. A background loop claims jobs, runs the pipeline
(download -> thumbnail + Rekognition copy -> face indexing) and records the result.

Because the queue lives in the database, nothing is lost if the server restarts:
unfinished jobs are simply picked up again.

Life of a photo:
    pending_upload  signed link handed out, file not confirmed yet
    uploaded        file is in S3, held back until the browser says the whole batch is done
                    (so processing doesn't compete with the upload for CPU and network);
                    released automatically if the browser goes quiet for UPLOAD_HOLD
    pending         waiting for a worker slot
    processed       done: thumbnail made and, if the event's plan includes face scan, faces indexed
                    (error_detail holds a warning if the face scan didn't work out)
    error           the file could not be read at all

Retries happen in ONE place: the queue. A failed photo is requeued with backoff up to
MAX_ATTEMPTS. Problems that retrying can't fix (a photo Rekognition will never accept) are
not retried, and Amazon's rate limit pauses the whole queue instead of every photo retrying
on its own.

Columns used: status, attempts, claimed_at, next_attempt_at, error_detail.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta

from botocore.exceptions import ClientError

from app.core.config import get_settings
from app.services.db import db
from app.services.rekognition_service import classify_error, describe_error, rekognition_service
from app.services.s3_service import s3_service
from app.services.thumbnail_service import thumbnail_service

settings = get_settings()

CONCURRENCY = max(1, settings.PHOTO_WORKER_CONCURRENCY)  # photos processed at once
MAX_ATTEMPTS = 4  # per photo, across restarts and retries
BACKOFF_BASE_SECONDS = 30  # wait before attempt n+1 is BASE * 2**(n-1)
STALE_CLAIM = timedelta(minutes=10)  # a claim older than this is a crashed/hung job
POLL_SECONDS = 5  # how often to look for due jobs when nothing wakes us
THROTTLE_PAUSE_SECONDS = 20  # how long the whole queue waits when Amazon says "slow down"
UPLOAD_HOLD = timedelta(minutes=20)  # how long an uploaded photo waits for its batch to finish

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


def _now() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# Holding photos back while a batch is still uploading
# ---------------------------------------------------------------------------

def hold_deadline() -> str:
    """When a freshly uploaded photo should be released if nobody asks for it sooner."""
    return (_now() + UPLOAD_HOLD).isoformat()


async def extend_hold(event_id: str) -> None:
    """The browser is still uploading this event's batch: push the release time back."""
    await db.execute(
        "UPDATE photos SET next_attempt_at = ? WHERE event_id = ? AND status = 'uploaded'",
        (hold_deadline(), event_id),
    )


async def release_event(event_id: str) -> None:
    """The batch is done uploading: let the worker start on this event's photos."""
    await db.execute(
        "UPDATE photos SET status = 'pending', next_attempt_at = NULL "
        "WHERE event_id = ? AND status = 'uploaded'",
        (event_id,),
    )
    notify()


async def _release_held() -> None:
    """Release photos whose hold ran out, e.g. because the browser tab was closed mid-upload."""
    await db.execute(
        "UPDATE photos SET status = 'pending', next_attempt_at = NULL "
        "WHERE status = 'uploaded' AND next_attempt_at <= ?",
        (_now().isoformat(),),
    )


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
    print(f"[worker] started (concurrency {CONCURRENCY})")


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
            await _release_held()
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

async def _process_safely(photo: dict) -> None:
    try:
        await _process(photo)
    except asyncio.CancelledError:
        raise  # shutting down; the claim is released on next start
    except Exception as e:
        print(f"[worker] unexpected error for {photo['id']}: {type(e).__name__}: {e}")
        await _fail(photo, f"Unexpected error: {type(e).__name__}: {e}")


async def _fail(photo: dict, reason: str, permanent: bool = False, throttled: bool = False) -> None:
    """Requeue with backoff, or mark as error once attempts are used up (or can't help)."""
    reason = reason[:500]
    if throttled:
        # Amazon's rate limit isn't this photo's fault: hand the attempt back and wait.
        await db.execute(
            "UPDATE photos SET claimed_at = NULL, attempts = MAX(attempts - 1, 0), "
            "next_attempt_at = ?, error_detail = ? WHERE id = ?",
            ((_now() + timedelta(seconds=THROTTLE_PAUSE_SECONDS)).isoformat(), reason, photo["id"]),
        )
        print(f"[worker] {photo['id']} waiting for Amazon's rate limit ({THROTTLE_PAUSE_SECONDS}s)")
        return
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
            await s3_service.download_file(s3_key, local)
        except Exception as e:
            missing = isinstance(e, ClientError) and e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound")
            return await _fail(photo, f"Could not read original from S3: {type(e).__name__}: {e}", permanent=missing)

        # Events without face scan (selection only) never call Rekognition
        event = await db.fetch_one("SELECT face_scan_enabled FROM events WHERE id = ?", (event_id,))
        face_scan = event is None or event["face_scan_enabled"] != 0

        # 2. Read the image once: size, thumbnail, and (for face scan) a JPEG copy sized for Rekognition.
        #    (A file that isn't a valid image will never succeed, so don't retry it.)
        try:
            prepared = await asyncio.to_thread(
                thumbnail_service.prepare, local, thumb_local, ai_copy=face_scan
            )
        except Exception as e:
            return await _fail(photo, f"Image read failed: {type(e).__name__}: {e}", permanent=True)

        # 3. Thumbnail upload (non-fatal; skipped if an earlier attempt already stored it)
        thumb_key = photo.get("thumbnail_s3_key")
        if not thumb_key:
            candidate = s3_thumbnail_key(event_id, photo_id)
            try:
                await s3_service.upload_file(
                    thumb_local, candidate, "image/jpeg",
                    cache_control="private, max-age=31536000, immutable",
                )
                thumb_key = candidate
            except Exception as e:
                print(f"[worker] thumbnail failed for {photo_id}: {type(e).__name__}: {e}")
        await db.execute(
            "UPDATE photos SET width = ?, height = ?, thumbnail_s3_key = ? WHERE id = ?",
            (prepared.width, prepared.height, thumb_key, photo_id),
        )

        # 4. Face indexing — only for events whose plan includes it, and skipped if faces are
        #    already stored, so a retry never duplicates them
        faces_count = (await db.fetch_one(
            "SELECT COUNT(*) AS n FROM faces WHERE photo_id = ?", (photo_id,)
        ))["n"]
        warning = None
        scan_hopeless = False  # Rekognition rejected this very image: another try changes nothing
        if face_scan and faces_count == 0:
            try:
                faces = await rekognition_service.index_image_bytes(prepared.ai_jpeg, photo_id, event_id)
                now = _now().isoformat()
                await db.executemany(
                    "INSERT INTO faces (id, photo_id, event_id, rekognition_face_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [(str(uuid.uuid4()), photo_id, event_id, f["rekognition_face_id"], now) for f in faces],
                )
                faces_count = len(faces)
            except Exception as e:
                kind = classify_error(e)
                if kind == "throttled":
                    rekognition_service.pause(THROTTLE_PAUSE_SECONDS)
                    return await _fail(photo, "Waiting for Amazon's rate limit before scanning faces", throttled=True)
                warning = describe_error(e)
                scan_hopeless = kind == "permanent"

        if warning and not scan_hopeless and photo["attempts"] < MAX_ATTEMPTS:
            return await _fail(photo, warning)  # try the whole job again later

        # 5. Done. A face scan that can't succeed still leaves a usable photo (it has its thumbnail
        #    and can be viewed and selected), with the warning kept so the dashboard shows it
        #    and its Retry button can offer it again.
        await db.execute(
            """
            UPDATE photos SET status = 'processed', claimed_at = NULL, next_attempt_at = NULL,
                faces_count = ?, error_detail = ?
            WHERE id = ?
            """,
            (faces_count, warning[:500] if warning else None, photo_id),
        )
        print(f"[worker] done {photo_id} | {faces_count} face(s) | {prepared.width}x{prepared.height}")
    finally:
        remove_silently(local)
        remove_silently(thumb_local)

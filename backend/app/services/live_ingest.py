"""
live_ingest.py — turns files that land in S3 under live/{event_id}/ into photos.

Live mode: a camera pushes photos over FTP/SFTP, the FTP server writes them to S3, and nobody tells
this app (there is no browser to call upload-complete). Two things find those files:

  1. An SQS queue that S3 sends "new object" notifications to (INGEST_QUEUE_URL, set up by
     backend/setup_live_ingest.py). New photos show up within a second or two.
  2. A periodic listing of the live folders of events that have live mode on. With no queue configured
     (laptops) this is the only path; with a queue it is a safety net for a missed notification.

Both end in ingest_object(), which is safe to call twice for the same file. It adds a `photos` row
with status 'pending' and source 'live'; photo_worker does the rest, exactly as for a browser upload.

Files are only ever added to the database here. Nothing in this module deletes anything from S3.
"""
import asyncio
import json
import os
import sqlite3
import uuid
from collections import Counter
from datetime import datetime
from urllib.parse import unquote_plus

import boto3
from botocore.config import Config

from app.core.config import get_settings
from app.services import photo_worker
from app.services.db import db
from app.services.s3_service import s3_service

settings = get_settings()

LIVE_PREFIX = "live/"  # live/{event_id}/{anything the camera sends}
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}  # same formats as browser uploads; RAW and sidecar files are left alone
OVER_LIMIT_MARKER = "Over this event's storage limit"  # start of error_detail; the Retry button skips these
SQS_WAIT_SECONDS = 10  # long poll; short enough that a restart isn't held up waiting for it

_lock: asyncio.Lock | None = None
_scan_now: asyncio.Event | None = None
_tasks: list[asyncio.Task] = []
_ignored_seen: dict[str, int] = {}  # event id -> how many non-image files we last reported for it


def _log(message: str) -> None:
    print(f"{datetime.now().strftime('%H:%M:%S')} [live] {message}", flush=True)


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def live_prefix(event_id: str) -> str:
    return f"{LIVE_PREFIX}{event_id}/"


# ---------------------------------------------------------------------------
# Reading keys and notifications
# ---------------------------------------------------------------------------

def parse_live_key(key: str) -> tuple[str, str] | None:
    """'live/{event_id}/cam1/IMG_1.JPG' -> ('{event_id}', 'cam1/IMG_1.JPG'); None for any other key."""
    if not key.startswith(LIVE_PREFIX):
        return None
    event_id, _, rest = key[len(LIVE_PREFIX):].partition("/")
    if not event_id or not rest or rest.endswith("/"):
        return None
    return event_id, rest


def parse_s3_event(body: str) -> list[tuple[str, int | None]]:
    """
    (key, size) of every newly created object in an S3 notification delivered through SQS.
    Returns [] for S3's test message and for events that aren't "object created".
    Raises ValueError for a body that isn't an S3 notification at all (a message nobody can use:
    it is left in the queue and ends up in the dead-letter queue, where it can be inspected).
    """
    data = json.loads(body)
    if not isinstance(data, dict):
        raise ValueError("message body is not a JSON object")
    if data.get("Event") == "s3:TestEvent":
        return []
    records = data.get("Records")
    if not isinstance(records, list):
        raise ValueError("message has no Records")
    found: list[tuple[str, int | None]] = []
    for rec in records:
        if not str(rec.get("eventName", "")).startswith("ObjectCreated"):
            continue
        s3 = rec.get("s3", {})
        bucket = s3.get("bucket", {}).get("name")
        if bucket and settings.S3_BUCKET_NAME and bucket != settings.S3_BUCKET_NAME:
            _log(f"ignored a notification for another bucket ({bucket})")
            continue
        obj = s3.get("object", {})
        if "key" not in obj:
            raise ValueError("record has no object key")
        found.append((unquote_plus(obj["key"]), obj.get("size")))  # keys arrive URL-encoded
    return found


# ---------------------------------------------------------------------------
# One file
# ---------------------------------------------------------------------------

async def _over_limit(event: dict, size: int) -> bool:
    capacity_gb = event.get("storage_capacity_gb")
    if not capacity_gb:  # legacy events without a capacity are unlimited
        return False
    used = (await db.fetch_one(
        "SELECT COALESCE(SUM(size_bytes), 0) AS used FROM photos WHERE event_id = ? AND status != 'pending_upload'",
        (event["id"],),
    ))["used"]
    return used + size > int(capacity_gb * 1024 ** 3)


def _size_text(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MB" if size >= 1024 * 1024 else f"{max(size // 1024, 1)} KB"


async def requeue_over_limit(event: dict) -> tuple[int, int]:
    """
    Retry for live photos that didn't fit the event's storage: queue the ones that fit now (the owner deleted
    photos, say). Returns (queued, still_too_big). The file's size is read from S3, because the row keeps 0 so
    that a photo that was not processed doesn't use up the storage the owner bought.
    """
    rows = await db.fetch_all(
        "SELECT id, s3_object_key FROM photos WHERE event_id = ? AND status = 'error' AND error_detail LIKE ? "
        "ORDER BY created_at",
        (event["id"], f"{OVER_LIMIT_MARKER}%"),
    )
    queued = left = 0
    for row in rows:
        try:
            size = await s3_service.get_object_size(row["s3_object_key"])
        except Exception:
            size = None
        if not size:  # gone from S3, or S3 can't be asked right now: leave it as it is
            left += 1
            continue
        async with _get_lock():
            if await _over_limit(event, size):
                left += 1
                continue
            await db.execute(
                "UPDATE photos SET status = 'pending', size_bytes = ?, error_detail = NULL, attempts = 0, "
                "claimed_at = NULL, next_attempt_at = NULL WHERE id = ?",
                (size, row["id"]),
            )
            queued += 1
    if queued:
        photo_worker.notify()
    return queued, left


async def ingest_object(key: str, size: int) -> str:
    """
    Register one S3 object as a photo. Safe to call again for the same key. Returns what happened:
      added          a new photo is queued for processing
      over_limit     the event's storage is full: recorded as a failed photo, file left in S3
      duplicate      this key is already registered
      ignored_event  the event is unknown, unpaid, or doesn't have live mode on
      ignored_file   not a JPG/PNG (RAW, sidecar, folder marker) or empty
    """
    parsed = parse_live_key(key)
    if parsed is None:
        return "ignored_file"
    event_id, relative = parsed
    if os.path.splitext(relative)[1].lower() not in IMAGE_EXTS or size <= 0:
        return "ignored_file"

    async with _get_lock():
        event = await db.fetch_one(
            "SELECT id, status, live_enabled, storage_capacity_gb FROM events WHERE id = ?", (event_id,)
        )
        if not event or event["status"] != "active" or not event["live_enabled"]:
            return "ignored_event"
        if await db.fetch_one("SELECT 1 AS found FROM photos WHERE s3_object_key = ?", (key,)):
            return "duplicate"

        over = await _over_limit(event, size)
        try:
            await db.execute(
                """
                INSERT INTO photos (id, event_id, original_file_name, s3_object_key, size_bytes,
                                    status, error_detail, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'live', ?)
                """,
                (
                    str(uuid.uuid4()),
                    event_id,
                    os.path.basename(relative),
                    key,
                    # An over-limit file stays in S3 but must not count against the storage the owner bought
                    0 if over else size,
                    "error" if over else "pending",
                    (f"{OVER_LIMIT_MARKER}: this {_size_text(size)} file was not processed. "
                     "Delete photos you don't need, then press Retry.") if over else None,
                    datetime.utcnow().isoformat(),
                ),
            )
        except sqlite3.IntegrityError:
            return "duplicate"  # the unique index caught a race we didn't
        return "over_limit" if over else "added"


async def _ingest_many(objects: list[tuple[str, int | None]]) -> Counter:
    counts: Counter = Counter()
    for key, size in objects:
        if size is None:  # notification without a size: ask S3
            size = await s3_service.get_object_size(key) or 0
        counts[await ingest_object(key, size)] += 1
    if counts["added"]:
        photo_worker.notify()
    return counts


def _report(counts: Counter, where: str) -> None:
    if counts["added"]:
        _log(f"{counts['added']} new photo(s) from {where}")
    if counts["over_limit"]:
        _log(f"  PROBLEM: {counts['over_limit']} photo(s) from {where} were not processed: the event's storage is full")


# ---------------------------------------------------------------------------
# Path 1: SQS notifications
# ---------------------------------------------------------------------------

def _sqs_client():
    return boto3.client(
        "sqs",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        config=Config(retries={"max_attempts": 3, "mode": "standard"}),
    )


async def _sqs_loop() -> None:
    client = _sqs_client()
    url = settings.INGEST_QUEUE_URL
    failures = 0
    while True:
        try:
            response = await asyncio.to_thread(
                client.receive_message,
                QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=SQS_WAIT_SECONDS,
            )
            if failures:
                _log(f"queue reachable again after {failures} failed attempt(s)")
                failures = 0

            finished = []  # handled, or deliberately ignored: take them off the queue
            counts: Counter = Counter()
            for i, message in enumerate(response.get("Messages", [])):
                try:
                    counts += await _ingest_many(parse_s3_event(message["Body"]))
                    finished.append({"Id": str(i), "ReceiptHandle": message["ReceiptHandle"]})
                except Exception as e:
                    # Left in the queue: it comes back after the visibility timeout and, if it keeps
                    # failing, lands in the dead-letter queue.
                    _log(f"  PROBLEM: queue message {message.get('MessageId')} failed "
                         f"({type(e).__name__}: {e}); it will be tried again")
            if finished:
                await asyncio.to_thread(client.delete_message_batch, QueueUrl=url, Entries=finished)
            _report(counts, "the queue")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            failures += 1
            if failures <= 3 or failures % 10 == 0:
                _log(f"  PROBLEM: cannot read the live queue ({type(e).__name__}: {e}); attempt {failures}. "
                     "New live photos are still found by the periodic scan.")
            await asyncio.sleep(min(5 * failures, 60))


# ---------------------------------------------------------------------------
# Path 2: listing the live folders
# ---------------------------------------------------------------------------

async def scan_live_events() -> Counter:
    """List the live folder of every active event with live mode on and register what isn't known yet."""
    total: Counter = Counter()
    events = await db.fetch_all("SELECT id FROM events WHERE status = 'active' AND live_enabled = 1")
    for event in events:
        event_id = event["id"]
        try:
            objects = await s3_service.list_prefix(live_prefix(event_id))
        except Exception as e:
            _log(f"  PROBLEM: could not list {live_prefix(event_id)} ({type(e).__name__}: {e})")
            continue
        known = {
            row["s3_object_key"]
            for row in await db.fetch_all(
                "SELECT s3_object_key FROM photos WHERE event_id = ? AND source = 'live'", (event_id,)
            )
        }
        counts = await _ingest_many([(key, size) for key, size in objects if key not in known])
        _report(counts, f"{live_prefix(event_id)}")
        # Files that aren't JPG/PNG are listed on every scan; say so once, and again only if the number changes
        if counts["ignored_file"] != _ignored_seen.get(event_id, 0):
            _ignored_seen[event_id] = counts["ignored_file"]
            if counts["ignored_file"]:
                _log(f"  note: {counts['ignored_file']} file(s) in {live_prefix(event_id)} are not JPG/PNG (or are empty) and are skipped")
        total += counts
    return total


def request_scan() -> None:
    """Look at the live folders right away (e.g. live mode was just switched on) instead of at the next interval."""
    if _scan_now is not None:
        _scan_now.set()


async def _scan_loop() -> None:
    interval = settings.LIVE_SAFETY_SCAN_SECONDS if settings.INGEST_QUEUE_URL else settings.LIVE_POLL_SECONDS
    interval = max(1, interval)
    while True:  # the first pass runs straight away: it picks up files that arrived while the server was down
        try:
            await scan_live_events()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _log(f"  PROBLEM: live scan failed ({type(e).__name__}: {e})")
        try:
            await asyncio.wait_for(_scan_now.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
        _scan_now.clear()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

async def start() -> None:
    """Start the background loops. Call once at app startup, after the DB is connected."""
    global _scan_now
    if _tasks:
        return
    _scan_now = asyncio.Event()
    _tasks.append(asyncio.create_task(_scan_loop(), name="live-scan"))
    if settings.INGEST_QUEUE_URL:
        _tasks.append(asyncio.create_task(_sqs_loop(), name="live-sqs"))
        _log(f"started (queue + a safety scan every {settings.LIVE_SAFETY_SCAN_SECONDS}s)")
    else:
        _log(f"started (no queue configured: scanning live folders every {settings.LIVE_POLL_SECONDS}s)")


async def stop() -> None:
    for task in _tasks:
        task.cancel()
    for task in _tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
    _tasks.clear()

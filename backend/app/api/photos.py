import os
import uuid
from typing import List
from datetime import datetime, timedelta
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Response, Depends
from fastapi.responses import RedirectResponse, JSONResponse
from app.api.auth import get_current_user
from app.core.config import get_settings
from app.services import photo_worker
from app.services.db import db
from app.services.event_access import get_owned_event, get_owned_active_event
from app.services.photo_worker import s3_original_key, remove_silently as _remove_silently
from app.services.s3_service import s3_service

router = APIRouter(prefix="/photos", tags=["photos"])
settings = get_settings()

# Uploads go browser -> S3 directly. The routes below only register a photo and
# queue it; photo_worker does the processing (and survives restarts).


# ---------------------------------------------------------------------------
# Direct-to-S3 upload: browser uploads straight to S3, server only signs + processes
# ---------------------------------------------------------------------------

STALE_UPLOAD_AFTER = timedelta(hours=1)  # a pending_upload older than this was abandoned
_IMAGE_EXTS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def _stale_cutoff() -> str:
    return (datetime.utcnow() - STALE_UPLOAD_AFTER).isoformat()


async def _check_capacity(event: dict, incoming: int) -> None:
    """Raise 413 if `incoming` bytes won't fit in the event's storage capacity."""
    capacity_gb = event.get("storage_capacity_gb")
    if not capacity_gb:  # legacy events without a capacity are unlimited
        return
    used = (await db.fetch_one(
        """
        SELECT COALESCE(SUM(size_bytes), 0) AS used FROM photos
        WHERE event_id = ? AND NOT (status = 'pending_upload' AND created_at < ?)
        """,
        (event["id"], _stale_cutoff()),
    ))["used"]
    capacity_bytes = int(capacity_gb * 1024 ** 3)
    if used + incoming > capacity_bytes:
        remaining_mb = max(capacity_bytes - used, 0) / (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=(
                f"Not enough storage: {incoming / (1024 * 1024):.1f} MB selected, "
                f"{remaining_mb:.1f} MB left of {capacity_gb:g} GB."
            ),
        )


class UploadFileSpec(BaseModel):
    name: str
    size: int = Field(gt=0)
    content_type: str


class UploadUrlsRequest(BaseModel):
    files: List[UploadFileSpec] = Field(min_length=1, max_length=500)


class UploadCompleteRequest(BaseModel):
    uploaded: List[str] = []
    failed: List[str] = []


@router.post("/upload-urls")
async def create_upload_urls(
    event_id: str,
    body: UploadUrlsRequest,
    user: dict = Depends(get_current_user),
):
    """Reserve a photo row per file and return a presigned S3 POST for each."""
    event = await get_owned_active_event(event_id, user)

    for f in body.files:
        if f.content_type not in _IMAGE_EXTS:
            raise HTTPException(status_code=400, detail=f"{f.name}: only JPG, PNG or WEBP images can be uploaded")
    await _check_capacity(event, sum(f.size for f in body.files))

    uploads = []
    for f in body.files:
        photo_id = str(uuid.uuid4())
        key = s3_original_key(event_id, photo_id, _IMAGE_EXTS[f.content_type])
        presigned = await s3_service.create_presigned_upload(key, f.content_type, f.size)
        await db.execute(
            """
            INSERT INTO photos (id, event_id, original_file_name, size_bytes, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (photo_id, event_id, f.name, f.size, "pending_upload", datetime.utcnow().isoformat()),
        )
        uploads.append({"photo_id": photo_id, "url": presigned["url"], "fields": presigned["fields"]})
    return {"uploads": uploads}


@router.post("/upload-complete")
async def complete_uploads(
    event_id: str,
    body: UploadCompleteRequest,
    user: dict = Depends(get_current_user),
):
    """Browser reports which direct uploads finished; start processing those."""
    await get_owned_active_event(event_id, user)

    async def _own_pending(ids: List[str]) -> list[dict]:
        rows = []
        for photo_id in ids:
            row = await db.fetch_one(
                """
                SELECT id, event_id, original_file_name, thumbnail_s3_key FROM photos
                WHERE id = ? AND event_id = ? AND status = 'pending_upload'
                """,
                (photo_id, event_id),
            )
            if row:
                rows.append(dict(row))
        return rows

    for photo in await _own_pending(body.failed):
        await db.execute("DELETE FROM photos WHERE id = ?", (photo["id"],))

    queued, missing = 0, 0
    for photo in await _own_pending(body.uploaded):
        # The key's extension came from the content type at signing time, which usually
        # matches the filename; try that first, then the other allowed extensions.
        ext = os.path.splitext(photo["original_file_name"] or "")[1].lstrip(".").lower()
        guess = "jpg" if ext == "jpeg" else ext
        key = size = None
        for candidate in dict.fromkeys([guess, *_IMAGE_EXTS.values()]):
            if candidate not in _IMAGE_EXTS.values():
                continue
            k = s3_original_key(event_id, photo["id"], candidate)
            size = await s3_service.get_object_size(k)
            if size is not None:
                key = k
                break
        if key is None:
            missing += 1
            await db.execute("DELETE FROM photos WHERE id = ?", (photo["id"],))
            continue
        # Queue it: the worker picks up status='pending' rows that have an S3 key.
        await db.execute(
            "UPDATE photos SET status = 'pending', s3_object_key = ?, size_bytes = ? WHERE id = ?",
            (key, size, photo["id"]),
        )
        queued += 1

    if queued:
        photo_worker.notify()
    return {"processing": queued, "missing": missing}


@router.post("/retry/{event_id}")
async def retry_failed_photos(
    event_id: str,
    user: dict = Depends(get_current_user),
):
    """
    Re-run processing for failed photos (status=error) and for photos whose
    face scan failed (processed with a warning). Needs the original in S3;
    photos that never reached S3 must be re-uploaded.
    """
    await get_owned_active_event(event_id, user)
    rows = await db.fetch_all(
        """
        SELECT id, event_id, original_file_name, s3_object_key, thumbnail_s3_key
        FROM photos
        WHERE event_id = ?
          AND (status = 'error' OR (status = 'processed' AND error_detail IS NOT NULL))
        """,
        (event_id,),
    )

    retrying, needs_reupload = 0, 0
    for row in rows:
        photo = dict(row)
        ext = os.path.splitext(photo["original_file_name"] or "")[1].lstrip(".").lower() or "jpg"
        s3_key = photo["s3_object_key"] or s3_original_key(event_id, photo["id"], ext)
        try:
            exists = await s3_service.object_exists(s3_key)
        except Exception:
            exists = False
        if not exists:
            needs_reupload += 1
            continue
        # Back in the queue with a fresh set of attempts.
        await db.execute(
            """
            UPDATE photos SET status = 'pending', s3_object_key = ?, error_detail = NULL,
                attempts = 0, claimed_at = NULL, next_attempt_at = NULL
            WHERE id = ?
            """,
            (s3_key, photo["id"]),
        )
        retrying += 1

    if retrying:
        photo_worker.notify()

    return {
        "retrying": retrying,
        "needs_reupload": needs_reupload,
        "message": f"Retrying {retrying} photo(s)"
        + (f"; {needs_reupload} must be re-uploaded" if needs_reupload else ""),
    }


# ---------------------------------------------------------------------------
# Sync — removed (Drive is gone)
# ---------------------------------------------------------------------------

@router.post("/sync/{event_id}")
async def start_sync(event_id: str):
    return Response(
        status_code=410,
        content=b"Drive sync removed. Upload photos from the dashboard (POST /photos/upload-urls).",
    )


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@router.get("/status/{event_id}")
async def get_event_status(event_id: str, user: dict = Depends(get_current_user)):
    event = await get_owned_event(event_id, user)

    counts = await db.fetch_one(
        """
        SELECT
            COUNT(*)                                        AS total,
            SUM(status = 'pending')                        AS pending,
            SUM(status = 'pending_upload' AND created_at >= ?) AS pending_upload,
            SUM(status = 'processed')                      AS processed,
            SUM(status = 'error')                          AS errors,
            COALESCE(SUM(faces_count), 0)                  AS total_faces
        FROM photos WHERE event_id = ?
        """,
        (_stale_cutoff(), event_id),
    )

    total = counts["total"] or 0
    processed = counts["processed"] or 0

    return {
        "event_id": event_id,
        "sync_status": event.get("sync_status", "idle"),
        "last_sync_at": event.get("last_sync_at"),
        "total": total,
        "pending": (counts["pending"] or 0) + (counts["pending_upload"] or 0),
        "processed": processed,
        "errors": counts["errors"] or 0,
        "total_faces": counts["total_faces"] or 0,
        "progress": (processed / total * 100) if total > 0 else 0,
    }


# ---------------------------------------------------------------------------
# Gallery — presigned thumbnail + original per photo, never crashes on null
# ---------------------------------------------------------------------------

@router.get("/event/{event_id}/gallery")
async def get_event_photos(
    event_id: str, page: int = 1, limit: int = 100, user: dict = Depends(get_current_user)
):
    await get_owned_event(event_id, user)
    offset = (page - 1) * limit

    count_result = await db.fetch_one(
        "SELECT COUNT(*) as total FROM photos WHERE event_id = ?", (event_id,)
    )
    total = count_result["total"]

    rows = await db.fetch_all(
        """
        SELECT id, original_file_name, s3_object_key, thumbnail_s3_key,
               width, height, faces_count, status, error_detail, created_at
        FROM photos
        WHERE event_id = ?
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        (event_id, limit, offset),
    )

    photos = []
    for p in rows:
        entry = dict(p)

        # presigned thumbnail URL — null if key missing or presign fails
        if p["thumbnail_s3_key"]:
            try:
                entry["presigned_thumbnail_url"] = await s3_service.get_presigned_url(
                    p["thumbnail_s3_key"], expiry_seconds=3600
                )
            except Exception as e:
                print(f"[gallery] presign thumbnail error for {p['id']}: {type(e).__name__}: {e}")
                entry["presigned_thumbnail_url"] = None
        else:
            entry["presigned_thumbnail_url"] = None

        # presigned original URL — null if key missing or presign fails
        if p["s3_object_key"]:
            try:
                entry["presigned_original_url"] = await s3_service.get_presigned_url(
                    p["s3_object_key"], expiry_seconds=3600
                )
            except Exception as e:
                print(f"[gallery] presign original error for {p['id']}: {type(e).__name__}: {e}")
                entry["presigned_original_url"] = None
        else:
            entry["presigned_original_url"] = None

        photos.append(entry)

    return {
        "photos": photos,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": (total + limit - 1) // limit if total > 0 else 0,
    }


# ---------------------------------------------------------------------------
# Stuck photo management — view and purge broken records
# ---------------------------------------------------------------------------

@router.get("/stuck/{event_id}")
async def get_stuck_photos(event_id: str, user: dict = Depends(get_current_user)):
    """
    Returns all photos for this event that are in an error or permanently
    pending state with no S3 key — i.e. will never be viewable.
    Use DELETE /photos/stuck/{event_id} to purge them.
    """
    await get_owned_event(event_id, user)
    rows = await db.fetch_all(
        """
        SELECT id, original_file_name, status, error_detail, created_at
        FROM photos
        WHERE event_id = ?
          AND (status = 'error' OR (status = 'pending' AND s3_object_key IS NULL)
               OR (status = 'pending_upload' AND created_at < ?))
        ORDER BY created_at DESC
        """,
        (event_id, _stale_cutoff()),
    )
    return {"stuck_count": len(rows), "photos": [dict(r) for r in rows]}


@router.delete("/stuck/{event_id}")
async def purge_stuck_photos(event_id: str, user: dict = Depends(get_current_user)):
    """
    Deletes all error/stuck photo rows for this event that have no S3 key.
    These are safe to remove — they were never successfully uploaded.
    """
    await get_owned_event(event_id, user)
    rows = await db.fetch_all(
        """
        SELECT id, s3_object_key, thumbnail_s3_key FROM photos
        WHERE event_id = ?
          AND (status = 'error' OR (status = 'pending' AND s3_object_key IS NULL)
               OR (status = 'pending_upload' AND created_at < ?))
        """,
        (event_id, _stale_cutoff()),
    )
    if not rows:
        return {"message": "No stuck photos found", "deleted_count": 0}

    deleted = 0
    for row in rows:
        photo_id = row["id"]
        for key in (row["s3_object_key"], row["thumbnail_s3_key"]):
            if key:
                try:
                    await s3_service.delete_object(key)
                except Exception as e:
                    print(f"[purge_stuck] could not delete {key}: {type(e).__name__}: {e}")
        # Clean up any leftover local staging files
        for ext in ["jpg", "jpeg", "png", "JPG", "JPEG", "PNG", "webp", "WEBP"]:
            _remove_silently(os.path.join(settings.UPLOAD_ROOT, f"{photo_id}.{ext}"))
        _remove_silently(os.path.join(settings.THUMBNAIL_ROOT, f"{photo_id}.jpg"))
        await db.execute("DELETE FROM faces WHERE photo_id = ?", (photo_id,))
        await db.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
        deleted += 1

    print(f"[purge_stuck] Deleted {deleted} stuck photo(s) for event {event_id}")
    return {"message": f"Deleted {deleted} stuck photo(s)", "deleted_count": deleted}


# ---------------------------------------------------------------------------
# Single-photo delete
# ---------------------------------------------------------------------------

@router.delete("/delete/{photo_id}")
async def delete_photo(photo_id: str, user: dict = Depends(get_current_user)):
    photo = await db.fetch_one(
        """
        SELECT p.* FROM photos p JOIN events e ON e.id = p.event_id
        WHERE p.id = ? AND e.owner_id = ?
        """,
        (photo_id, user["id"]),
    )
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    try:
        if photo.get("s3_object_key"):
            await s3_service.delete_object(photo["s3_object_key"])
        if photo.get("thumbnail_s3_key"):
            await s3_service.delete_object(photo["thumbnail_s3_key"])

        for ext in ["jpg", "jpeg", "png", "JPG", "JPEG", "PNG", "webp", "WEBP"]:
            _remove_silently(os.path.join(settings.UPLOAD_ROOT, f"{photo_id}.{ext}"))
        _remove_silently(os.path.join(settings.THUMBNAIL_ROOT, f"{photo_id}.jpg"))

        await db.execute("DELETE FROM faces WHERE photo_id = ?", (photo_id,))
        await db.execute("DELETE FROM photos WHERE id = ?", (photo_id,))

        return {"message": "Photo deleted successfully"}
    except Exception as e:
        print(f"[delete_photo] Error for {photo_id}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting photo: {e}")


# ---------------------------------------------------------------------------
# Bulk delete
# ---------------------------------------------------------------------------

@router.post("/delete/bulk")
async def delete_photos_bulk(photo_ids: List[str], user: dict = Depends(get_current_user)):
    deleted_count = 0
    errors = []

    for photo_id in photo_ids:
        try:
            photo = await db.fetch_one(
                """
                SELECT p.* FROM photos p JOIN events e ON e.id = p.event_id
                WHERE p.id = ? AND e.owner_id = ?
                """,
                (photo_id, user["id"]),
            )
            if not photo:
                errors.append(f"Photo {photo_id} not found")
                continue

            if photo.get("s3_object_key"):
                await s3_service.delete_object(photo["s3_object_key"])
            if photo.get("thumbnail_s3_key"):
                await s3_service.delete_object(photo["thumbnail_s3_key"])

            for ext in ["jpg", "jpeg", "png", "JPG", "JPEG", "PNG", "webp", "WEBP"]:
                _remove_silently(os.path.join(settings.UPLOAD_ROOT, f"{photo_id}.{ext}"))
            _remove_silently(os.path.join(settings.THUMBNAIL_ROOT, f"{photo_id}.jpg"))

            await db.execute("DELETE FROM faces WHERE photo_id = ?", (photo_id,))
            await db.execute("DELETE FROM photos WHERE id = ?", (photo_id,))
            deleted_count += 1
        except Exception as e:
            errors.append(f"Error deleting {photo_id}: {type(e).__name__}: {e}")
            print(f"[bulk_delete] Error for {photo_id}: {type(e).__name__}: {e}")

    return {
        "message": f"Deleted {deleted_count} photos",
        "deleted_count": deleted_count,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Serving — graceful 202 when photo is still processing, not a hard 404
# ---------------------------------------------------------------------------

@router.get("/original/{photo_id}")
async def get_original(photo_id: str):
    """Redirects to presigned S3 URL for the original. Returns 202 if still processing."""
    row = await db.fetch_one(
        "SELECT s3_object_key, original_file_name, status FROM photos WHERE id = ?",
        (photo_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Photo not found")
    if not row["s3_object_key"]:
        return JSONResponse(
            status_code=202,
            content={"detail": "Photo is still processing", "status": row["status"]},
        )

    try:
        url = await s3_service.get_presigned_url(row["s3_object_key"], expiry_seconds=3600)
        return RedirectResponse(url=url, status_code=302)
    except Exception as e:
        print(f"[get_original] presign error for {photo_id}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Could not generate download URL")


@router.get("/thumbnail/{photo_id}")
async def get_thumbnail(photo_id: str):
    """Redirects to presigned S3 URL for the thumbnail. Returns 202 if still processing."""
    row = await db.fetch_one(
        "SELECT thumbnail_s3_key, status FROM photos WHERE id = ?", (photo_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Photo not found")
    if not row["thumbnail_s3_key"]:
        return JSONResponse(
            status_code=202,
            content={"detail": "Thumbnail is still processing", "status": row["status"]},
        )

    try:
        url = await s3_service.get_presigned_url(row["thumbnail_s3_key"], expiry_seconds=3600)
        return RedirectResponse(url=url, status_code=302)
    except Exception as e:
        print(f"[get_thumbnail] presign error for {photo_id}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Could not generate thumbnail URL")


@router.get("/download/{photo_id}")
async def download_photo(photo_id: str):
    """Redirects to presigned download URL. Returns 202 if still processing."""
    row = await db.fetch_one(
        "SELECT s3_object_key, original_file_name, status FROM photos WHERE id = ?",
        (photo_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Photo not found")
    if not row["s3_object_key"]:
        return JSONResponse(
            status_code=202,
            content={"detail": "Photo is still processing", "status": row["status"]},
        )

    try:
        filename = row.get("original_file_name") or f"{photo_id}.jpg"
        url = await s3_service.get_presigned_download_url(
            row["s3_object_key"], filename, expiry_seconds=3600
        )
        return RedirectResponse(url=url, status_code=302)
    except Exception as e:
        print(f"[download_photo] presign error for {photo_id}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Could not generate download URL")

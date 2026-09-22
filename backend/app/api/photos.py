import os
import shutil
import uuid
import asyncio
from typing import List
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks, Response
from fastapi.responses import RedirectResponse, JSONResponse
from PIL import Image
from app.core.config import get_settings
from app.services.db import db
from app.services.s3_service import s3_service
from app.services.rekognition_service import rekognition_service
from app.services.thumbnail_service import thumbnail_service

router = APIRouter(prefix="/photos", tags=["photos"])
settings = get_settings()

# Local staging dirs (originals live here briefly before S3 upload)
os.makedirs(settings.UPLOAD_ROOT, exist_ok=True)
os.makedirs(settings.THUMBNAIL_ROOT, exist_ok=True)


# ---------------------------------------------------------------------------
# S3 key helpers
# ---------------------------------------------------------------------------

def s3_original_key(event_id: str, photo_id: str, ext: str) -> str:
    return f"events/{event_id}/originals/{photo_id}.{ext}"

def s3_thumbnail_key(event_id: str, photo_id: str) -> str:
    return f"events/{event_id}/thumbnails/{photo_id}.jpg"


# ---------------------------------------------------------------------------
# Background processing — granular error reporting per step
# ---------------------------------------------------------------------------

async def process_photo(
    photo_id: str,
    event_id: str,
    event_slug: str,
    original_path: str,
    filename: str,
):
    """
    Full processing pipeline:
    1. Read image dimensions.
    2. Upload original to S3.
    3. Generate thumbnail locally, upload to S3.
    4. Index faces with Rekognition (against the event collection).
    5. Store face rows in the faces table.
    6. Update photos row to status=processed.
    7. Remove local staging files.
    """

    def _mark_error(reason: str):
        """Fire-and-forget DB update — called from except blocks."""
        import asyncio as _asyncio
        async def _update():
            await db.execute(
                "UPDATE photos SET status = ?, error_detail = ? WHERE id = ?",
                ("error", reason[:500], photo_id),
            )
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_update())
        except Exception:
            pass  # best-effort

    # ── Step 0: verify staging file exists ──────────────────────────────────
    if not os.path.exists(original_path):
        msg = f"[process_photo] Staging file missing for {photo_id}: {original_path}"
        print(msg)
        await db.execute(
            "UPDATE photos SET status = ?, error_detail = ? WHERE id = ?",
            ("error", "Staging file missing", photo_id),
        )
        return

    try:
        file_ext = os.path.splitext(filename)[1].lstrip(".").lower() or "jpg"

        # ── Step 1: image dimensions ─────────────────────────────────────────
        try:
            def get_image_size(path: str):
                with Image.open(path) as img:
                    return img.size
            width, height = await asyncio.to_thread(get_image_size, original_path)
            print(f"[process_photo] {photo_id} | dimensions: {width}x{height}")
        except Exception as e:
            print(f"[process_photo] STEP 1 FAILED — image read error for {photo_id}: {e}")
            await db.execute(
                "UPDATE photos SET status = ?, error_detail = ? WHERE id = ?",
                ("error", f"Image read failed: {type(e).__name__}: {e}"[:500], photo_id),
            )
            _remove_silently(original_path)
            return

        # ── Step 2: upload original to S3 ────────────────────────────────────
        s3_key = s3_original_key(event_id, photo_id, file_ext)
        try:
            content_type = _content_type(file_ext)
            await s3_service.upload_file(original_path, s3_key, content_type)
            print(f"[process_photo] {photo_id} | S3 original uploaded: {s3_key}")
        except Exception as e:
            msg = f"S3 upload failed: {type(e).__name__}: {e}"
            print(f"[process_photo] STEP 2 FAILED — {msg}")
            await db.execute(
                "UPDATE photos SET status = ?, error_detail = ? WHERE id = ?",
                ("error", msg[:500], photo_id),
            )
            _remove_silently(original_path)
            return

        # ── Step 3: generate thumbnail and upload to S3 ──────────────────────
        thumb_local = os.path.join(settings.THUMBNAIL_ROOT, f"{photo_id}.jpg")
        thumb_s3_key = s3_thumbnail_key(event_id, photo_id)
        try:
            await asyncio.to_thread(
                thumbnail_service.generate_thumbnail, original_path, thumb_local
            )
            await s3_service.upload_file(thumb_local, thumb_s3_key, "image/jpeg")
            print(f"[process_photo] {photo_id} | S3 thumbnail uploaded: {thumb_s3_key}")
        except Exception as e:
            print(f"[process_photo] STEP 3 FAILED — thumbnail for {photo_id}: {type(e).__name__}: {e}")
            # Still continue — we have the original; thumbnail failure is non-fatal
            thumb_s3_key = None

        # ── Step 4: Rekognition face indexing ────────────────────────────────
        try:
            faces_data = await rekognition_service.index_photo_faces(
                s3_key, photo_id, event_id
            )
            print(f"[process_photo] {photo_id} | Rekognition indexed {len(faces_data)} face(s)")
        except Exception as e:
            msg = f"Rekognition failed: {type(e).__name__}: {e}"
            print(f"[process_photo] STEP 4 FAILED — {msg}")
            faces_data = []
            # store a warning but don't fail the photo — original is already on S3
            await db.execute(
                "UPDATE photos SET error_detail = ? WHERE id = ?",
                (msg[:500], photo_id),
            )

        # ── Step 5: persist face rows ────────────────────────────────────────
        now = datetime.utcnow().isoformat()
        for face in faces_data:
            try:
                face_id = str(uuid.uuid4())
                await db.execute(
                    """
                    INSERT INTO faces (id, photo_id, event_id, rekognition_face_id, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (face_id, photo_id, event_id, face["rekognition_face_id"], now),
                )
            except Exception as e:
                print(f"[process_photo] STEP 5 FAILED — face insert for {photo_id}: {e}")

        # ── Step 6: mark photo as processed ─────────────────────────────────
        await db.execute(
            """
            UPDATE photos SET
                s3_object_key    = ?,
                thumbnail_s3_key = ?,
                width            = ?,
                height           = ?,
                faces_count      = ?,
                status           = ?
            WHERE id = ?
            """,
            (s3_key, thumb_s3_key, width, height, len(faces_data), "processed", photo_id),
        )

        # ── Step 7: clean up local staging files ────────────────────────────
        _remove_silently(original_path)
        if thumb_s3_key:
            _remove_silently(thumb_local)

        print(
            f"[process_photo] ✓ Done: {photo_id} | "
            f"{len(faces_data)} face(s) | {width}x{height}"
        )

    except Exception as e:
        # Catch-all safety net — should rarely be reached with per-step handling above
        msg = f"Unexpected error: {type(e).__name__}: {e}"
        print(f"[process_photo] UNEXPECTED ERROR for {photo_id}: {msg}")
        await db.execute(
            "UPDATE photos SET status = ?, error_detail = ? WHERE id = ?",
            ("error", msg[:500], photo_id),
        )


def _content_type(ext: str) -> str:
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "gif": "image/gif",
    }.get(ext.lower(), "image/jpeg")


def _remove_silently(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print(f"[cleanup] Could not remove {path}: {e}")


# ---------------------------------------------------------------------------
# Upload endpoint
# ---------------------------------------------------------------------------

@router.post("/upload")
async def upload_photos(
    background_tasks: BackgroundTasks,
    event_id: str,
    files: List[UploadFile] = File(...),
):
    row = await db.fetch_one("SELECT slug FROM events WHERE id = ?", (event_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    event_slug = row["slug"]

    processed_count = 0
    for file in files:
        photo_id = str(uuid.uuid4())
        file_ext = file.filename.rsplit(".", 1)[-1] if "." in file.filename else "jpg"
        original_path = os.path.join(settings.UPLOAD_ROOT, f"{photo_id}.{file_ext}")

        with open(original_path, "wb") as buf:
            shutil.copyfileobj(file.file, buf)

        await db.execute(
            """
            INSERT INTO photos (id, event_id, original_file_name, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (photo_id, event_id, file.filename, "pending", datetime.utcnow().isoformat()),
        )

        background_tasks.add_task(
            process_photo, photo_id, event_id, event_slug, original_path, file.filename
        )
        processed_count += 1

    return {
        "message": f"Successfully started processing {processed_count} photos",
        "event_id": event_id,
    }


# ---------------------------------------------------------------------------
# Sync — removed (Drive is gone)
# ---------------------------------------------------------------------------

@router.post("/sync/{event_id}")
async def start_sync(event_id: str):
    return Response(
        status_code=410,
        content=b"Drive sync removed. Upload photos directly via POST /photos/upload.",
    )


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

@router.get("/status/{event_id}")
async def get_event_status(event_id: str):
    row = await db.fetch_one("SELECT * FROM events WHERE id = ?", (event_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    event = dict(row)

    counts = await db.fetch_one(
        """
        SELECT
            COUNT(*)                                        AS total,
            SUM(status = 'pending')                        AS pending,
            SUM(status = 'pending_upload')                 AS pending_upload,
            SUM(status = 'processed')                      AS processed,
            SUM(status = 'error')                          AS errors,
            COALESCE(SUM(faces_count), 0)                  AS total_faces
        FROM photos WHERE event_id = ?
        """,
        (event_id,),
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
async def get_event_photos(event_id: str, page: int = 1, limit: int = 100):
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
async def get_stuck_photos(event_id: str):
    """
    Returns all photos for this event that are in an error or permanently
    pending state with no S3 key — i.e. will never be viewable.
    Use DELETE /photos/stuck/{event_id} to purge them.
    """
    rows = await db.fetch_all(
        """
        SELECT id, original_file_name, status, error_detail, created_at
        FROM photos
        WHERE event_id = ?
          AND (status = 'error' OR (status = 'pending' AND s3_object_key IS NULL))
        ORDER BY created_at DESC
        """,
        (event_id,),
    )
    return {"stuck_count": len(rows), "photos": [dict(r) for r in rows]}


@router.delete("/stuck/{event_id}")
async def purge_stuck_photos(event_id: str):
    """
    Deletes all error/stuck photo rows for this event that have no S3 key.
    These are safe to remove — they were never successfully uploaded.
    """
    rows = await db.fetch_all(
        """
        SELECT id FROM photos
        WHERE event_id = ?
          AND (status = 'error' OR (status = 'pending' AND s3_object_key IS NULL))
        """,
        (event_id,),
    )
    if not rows:
        return {"message": "No stuck photos found", "deleted_count": 0}

    deleted = 0
    for row in rows:
        photo_id = row["id"]
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
async def delete_photo(photo_id: str):
    photo = await db.fetch_one("SELECT * FROM photos WHERE id = ?", (photo_id,))
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
async def delete_photos_bulk(photo_ids: List[str]):
    deleted_count = 0
    errors = []

    for photo_id in photo_ids:
        try:
            photo = await db.fetch_one("SELECT * FROM photos WHERE id = ?", (photo_id,))
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

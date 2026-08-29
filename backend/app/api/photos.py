import os
import shutil
import uuid
import json
import asyncio
import io
from typing import List
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks, Response
from fastapi.responses import RedirectResponse
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
# Background processing
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
    5. Store face rows (rekognition_face_id) in the faces table.
    6. Update photos row: s3_object_key, thumbnail_s3_key, dimensions,
       faces_count, status=processed.
    7. Remove local staging files.
    """
    try:
        file_ext = os.path.splitext(filename)[1].lstrip(".").lower() or "jpg"

        # 1. Image dimensions (cheap, local)
        def get_image_size(path: str):
            with Image.open(path) as img:
                return img.size

        width, height = await asyncio.to_thread(get_image_size, original_path)

        # 2. Upload original to S3
        s3_key = s3_original_key(event_id, photo_id, file_ext)
        content_type = _content_type(file_ext)
        await s3_service.upload_file(original_path, s3_key, content_type)

        # 3. Thumbnail — generate locally then push to S3
        thumb_local = os.path.join(settings.THUMBNAIL_ROOT, f"{photo_id}.jpg")
        await asyncio.to_thread(
            thumbnail_service.generate_thumbnail, original_path, thumb_local
        )
        thumb_s3_key = s3_thumbnail_key(event_id, photo_id)
        await s3_service.upload_file(thumb_local, thumb_s3_key, "image/jpeg")

        # 4. Rekognition face indexing — image is already in S3
        faces_data = await rekognition_service.index_photo_faces(
            s3_key, photo_id, event_id
        )

        # 5. Persist face rows
        now = datetime.utcnow().isoformat()
        for face in faces_data:
            face_id = str(uuid.uuid4())
            await db.execute(
                """
                INSERT INTO faces (id, photo_id, event_id, rekognition_face_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (face_id, photo_id, event_id, face["rekognition_face_id"], now),
            )

        # 6. Update photo record
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

        # 7. Clean up local staging files
        _remove_silently(original_path)
        _remove_silently(thumb_local)

        print(
            f"[process_photo] Done: {photo_id} | "
            f"{len(faces_data)} face(s) indexed | {width}x{height}"
        )

    except Exception as e:
        print(f"[process_photo] Error for {photo_id}: {e}")
        await db.execute(
            "UPDATE photos SET status = ? WHERE id = ?", ("error", photo_id)
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
# Gallery — includes presigned thumbnail URL per photo
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
               width, height, faces_count, status, created_at
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
        # Add presigned thumbnail URL when the key is available
        if p["thumbnail_s3_key"]:
            try:
                entry["presigned_thumbnail_url"] = await s3_service.get_presigned_url(
                    p["thumbnail_s3_key"], expiry_seconds=3600
                )
            except Exception as e:
                print(f"[gallery] presign error for {p['id']}: {e}")
                entry["presigned_thumbnail_url"] = None
        else:
            entry["presigned_thumbnail_url"] = None
        photos.append(entry)

    return {
        "photos": photos,
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": (total + limit - 1) // limit if total > 0 else 0,
    }


# ---------------------------------------------------------------------------
# Single-photo delete
# ---------------------------------------------------------------------------

@router.delete("/delete/{photo_id}")
async def delete_photo(photo_id: str):
    photo = await db.fetch_one("SELECT * FROM photos WHERE id = ?", (photo_id,))
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")

    try:
        # S3 cleanup
        if photo.get("s3_object_key"):
            await s3_service.delete_object(photo["s3_object_key"])
        if photo.get("thumbnail_s3_key"):
            await s3_service.delete_object(photo["thumbnail_s3_key"])

        # Local staging cleanup (may still be present if processing failed mid-way)
        for ext in ["jpg", "jpeg", "png", "JPG", "JPEG", "PNG", "webp", "WEBP"]:
            _remove_silently(os.path.join(settings.UPLOAD_ROOT, f"{photo_id}.{ext}"))
        _remove_silently(os.path.join(settings.THUMBNAIL_ROOT, f"{photo_id}.jpg"))

        await db.execute("DELETE FROM faces WHERE photo_id = ?", (photo_id,))
        await db.execute("DELETE FROM photos WHERE id = ?", (photo_id,))

        return {"message": "Photo deleted successfully"}
    except Exception as e:
        print(f"[delete_photo] Error for {photo_id}: {e}")
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
            errors.append(f"Error deleting photo {photo_id}: {e}")
            print(f"[bulk_delete] Error for {photo_id}: {e}")

    return {
        "message": f"Deleted {deleted_count} photos",
        "deleted_count": deleted_count,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Serving — all return presigned S3 URLs
# ---------------------------------------------------------------------------

@router.get("/original/{photo_id}")
async def get_original(photo_id: str):
    """Returns a presigned S3 URL for the original image (redirects browser)."""
    row = await db.fetch_one(
        "SELECT s3_object_key, original_file_name FROM photos WHERE id = ?", (photo_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Photo not found")
    if not row["s3_object_key"]:
        raise HTTPException(status_code=404, detail="Photo not yet uploaded to S3")

    url = await s3_service.get_presigned_url(row["s3_object_key"], expiry_seconds=3600)
    return RedirectResponse(url=url, status_code=302)


@router.get("/thumbnail/{photo_id}")
async def get_thumbnail(photo_id: str):
    """Returns a presigned S3 URL for the thumbnail (redirects browser)."""
    row = await db.fetch_one(
        "SELECT thumbnail_s3_key FROM photos WHERE id = ?", (photo_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Photo not found")
    if not row["thumbnail_s3_key"]:
        raise HTTPException(status_code=404, detail="Thumbnail not yet uploaded to S3")

    url = await s3_service.get_presigned_url(row["thumbnail_s3_key"], expiry_seconds=3600)
    return RedirectResponse(url=url, status_code=302)


@router.get("/download/{photo_id}")
async def download_photo(photo_id: str):
    """Returns a presigned S3 URL that forces a file download."""
    row = await db.fetch_one(
        "SELECT s3_object_key, original_file_name FROM photos WHERE id = ?", (photo_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Photo not found")
    if not row["s3_object_key"]:
        raise HTTPException(status_code=404, detail="Photo not yet uploaded to S3")

    filename = row.get("original_file_name") or f"{photo_id}.jpg"
    url = await s3_service.get_presigned_download_url(
        row["s3_object_key"], filename, expiry_seconds=3600
    )
    return RedirectResponse(url=url, status_code=302)

import asyncio
import os
import shutil
import uuid
import json
import io
import zipfile
from datetime import datetime
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, BackgroundTasks, Response, Depends
from fastapi.responses import RedirectResponse
from app.api.auth import get_current_user
from app.services.db import db
from app.services.event_access import get_owned_event
from app.services.s3_service import s3_service
from app.services.rekognition_service import rekognition_service
from app.core.config import get_settings

router = APIRouter(prefix="/guests", tags=["guests"])
settings = get_settings()

# Local staging dir for selfies (file lives here only until S3 upload completes)
os.makedirs(settings.GUEST_SELFIES_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# S3 key helper
# ---------------------------------------------------------------------------

def s3_selfie_key(event_id: str, request_id: str, ext: str) -> str:
    return f"events/{event_id}/selfies/{request_id}.{ext}"


# ---------------------------------------------------------------------------
# Background processing — Rekognition-based matching
# ---------------------------------------------------------------------------

async def process_guest_request(
    request_id: str,
    event_id: str,
    event_slug: str,
    selfie_path: str,
):
    """
    New flow (3 steps, no linear scan):

    1. Read selfie bytes from local staging file.
    2. Call rekognition_service.search_guest_selfie(image_bytes, event_id).
       Rekognition returns matches directly — photo_id comes from ExternalImageId
       set during IndexFaces; no SQLite faces table scan needed.
    3. Deduplicate by photo_id (already done in service), sort by similarity desc,
       store matched_photo_ids.
    4. Upload selfie to S3 for record-keeping, remove local file.
    """
    try:
        # 1. Read selfie bytes
        try:
            with open(selfie_path, "rb") as f:
                image_bytes = f.read()
        except OSError as e:
            await db.execute(
                "UPDATE guests SET status = ?, error = ? WHERE id = ?",
                ("error", f"Could not read selfie: {e}", request_id),
            )
            return

        if not image_bytes:
            await db.execute(
                "UPDATE guests SET status = ?, error = ? WHERE id = ?",
                ("error", "Selfie file is empty", request_id),
            )
            return

        # 2. Search Rekognition collection
        matches = await rekognition_service.search_guest_selfie(image_bytes, event_id)

        # matches is already deduplicated & sorted by the service layer
        matched_photo_ids = [m["photo_id"] for m in matches]

        # 3. Persist results
        await db.execute(
            """
            UPDATE guests SET
                status           = ?,
                match_count      = ?,
                matched_photo_ids = ?
            WHERE id = ?
            """,
            ("completed", len(matched_photo_ids), json.dumps(matched_photo_ids), request_id),
        )

        # 4. Upload selfie to S3 then clean up locally
        file_ext = os.path.splitext(selfie_path)[1].lstrip(".").lower() or "jpg"
        selfie_s3_key = s3_selfie_key(event_id, request_id, file_ext)
        try:
            await s3_service.upload_file(selfie_path, selfie_s3_key, "image/jpeg")
            # Update selfie_path to the S3 key so the serving endpoint can use it
            await db.execute(
                "UPDATE guests SET selfie_path = ? WHERE id = ?",
                (selfie_s3_key, request_id),
            )
        except Exception as e:
            print(f"[process_guest_request] Selfie S3 upload failed for {request_id}: {e}")
            # Non-fatal — matching already succeeded

        # Remove local staging file regardless
        _remove_silently(selfie_path)

        print(
            f"[process_guest_request] Done: {request_id} | "
            f"{len(matched_photo_ids)} match(es)"
        )

    except Exception as e:
        print(f"[process_guest_request] Fatal error for {request_id}: {e}")
        await db.execute(
            "UPDATE guests SET status = ?, error = ? WHERE id = ?",
            ("error", str(e), request_id),
        )


def _remove_silently(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print(f"[cleanup] Could not remove {path}: {e}")


# ---------------------------------------------------------------------------
# Submit guest selfie request
# ---------------------------------------------------------------------------

@router.post("/request")
async def guest_request(
    background_tasks: BackgroundTasks,
    event_slug: str = Form(...),
    name: str = Form(None),
    email: str = Form(None),
    phone: str = Form(None),
    secret_code: str = Form(None),
    selfie: UploadFile = File(...),
):
    row = await db.fetch_one(
        "SELECT id, secret_code FROM events WHERE slug = ? AND status = 'active'", (event_slug,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")

    event_id = row["id"]
    expected_code = row["secret_code"]

    if expected_code and expected_code != secret_code:
        raise HTTPException(status_code=401, detail="Invalid secret code")

    # Name/email are optional. When given, return the existing request for the same pair.
    existing = None
    if name and email:
        existing = await db.fetch_one(
            "SELECT * FROM guests WHERE event_id = ? AND name = ? AND email = ?",
            (event_id, name, email),
        )
    if existing:
        return {
            "message": "Found your existing request!",
            "request_id": existing["id"],
            "status": existing["status"],
        }

    request_id = str(uuid.uuid4())
    name = name or f"Guest {request_id[:6]}"
    email = email or ""
    file_ext = selfie.filename.rsplit(".", 1)[-1] if "." in selfie.filename else "jpg"
    selfie_path = os.path.join(settings.GUEST_SELFIES_DIR, f"{request_id}.{file_ext}")

    with open(selfie_path, "wb") as buf:
        shutil.copyfileobj(selfie.file, buf)

    await db.execute(
        """
        INSERT INTO guests (id, event_id, name, email, phone, selfie_path, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request_id, event_id, name, email, phone,
            selfie_path, "processing", datetime.utcnow().isoformat(),
        ),
    )

    background_tasks.add_task(
        process_guest_request, request_id, event_id, event_slug, selfie_path
    )
    return {"message": "Your photos are being processed.", "request_id": request_id}


# ---------------------------------------------------------------------------
# Status poll
# ---------------------------------------------------------------------------

@router.get("/status/{request_id}")
async def get_guest_request_status(request_id: str):
    row = await db.fetch_one("SELECT * FROM guests WHERE id = ?", (request_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")
    return {
        "status": row["status"],
        "match_count": row.get("match_count", 0),
        "error": row.get("error"),
    }


# ---------------------------------------------------------------------------
# Admin: list guests for an event
# ---------------------------------------------------------------------------

@router.get("/event/{event_id}")
async def get_event_guests(event_id: str, user: dict = Depends(get_current_user)):
    await get_owned_event(event_id, user)
    rows = await db.fetch_all(
        """
        SELECT id, name, email, phone, selfie_path, status, match_count, created_at
        FROM guests
        WHERE event_id = ?
        ORDER BY created_at DESC
        """,
        (event_id,),
    )
    event = await db.fetch_one("SELECT slug FROM events WHERE id = ?", (event_id,))

    guests = []
    for row in rows:
        guests.append(
            {
                "id": row["id"],
                "name": row["name"],
                "email": row["email"],
                "phone": row.get("phone"),
                "status": row["status"],
                "match_count": row.get("match_count", 0),
                "created_at": row["created_at"],
                "gallery_link": f"/event/{event['slug']}/guest/{row['id']}"
                if event
                else None,
            }
        )
    return {"guests": guests, "total": len(guests)}


# ---------------------------------------------------------------------------
# Selfie avatar — presigned S3 redirect (S3 key stored after upload)
# or local fallback if still processing
# ---------------------------------------------------------------------------

@router.get("/selfie/{guest_id}")
async def get_guest_selfie(guest_id: str):
    row = await db.fetch_one("SELECT selfie_path FROM guests WHERE id = ?", (guest_id,))
    if not row or not row.get("selfie_path"):
        raise HTTPException(status_code=404, detail="Selfie not found")

    selfie_ref = row["selfie_path"]

    # If it looks like an S3 key (no OS path separators, starts with "events/")
    if selfie_ref.startswith("events/"):
        url = await s3_service.get_presigned_url(selfie_ref, expiry_seconds=3600)
        return RedirectResponse(url=url, status_code=302)

    # Local fallback (still processing or upload failed)
    if os.path.exists(selfie_ref):
        from fastapi.responses import FileResponse
        return FileResponse(selfie_ref)

    raise HTTPException(status_code=404, detail="Selfie not found")


# ---------------------------------------------------------------------------
# Delete guest (admin — allows re-submission)
# ---------------------------------------------------------------------------

@router.delete("/{guest_id}")
async def delete_guest(guest_id: str, user: dict = Depends(get_current_user)):
    row = await db.fetch_one(
        """
        SELECT g.selfie_path FROM guests g JOIN events e ON e.id = g.event_id
        WHERE g.id = ? AND e.owner_id = ?
        """,
        (guest_id, user["id"]),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Guest not found")

    selfie_ref = row.get("selfie_path")
    if selfie_ref:
        if selfie_ref.startswith("events/"):
            # S3 object
            try:
                await s3_service.delete_object(selfie_ref)
            except Exception as e:
                print(f"[delete_guest] S3 selfie delete error: {e}")
        else:
            # Local file (processing failed before S3 upload)
            _remove_silently(selfie_ref)

    await db.execute("DELETE FROM guests WHERE id = ?", (guest_id,))
    return {"message": "Guest deleted successfully"}


# ---------------------------------------------------------------------------
# Matches — paginated, with presigned URLs
# ---------------------------------------------------------------------------

@router.get("/{request_id}/matches")
async def get_guest_matches(request_id: str, page: int = 1, limit: int = 50):
    row = await db.fetch_one("SELECT * FROM guests WHERE id = ?", (request_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")

    photo_ids = json.loads(row.get("matched_photo_ids") or "[]")
    total_matches = len(photo_ids)

    start = (page - 1) * limit
    paged_ids = photo_ids[start: start + limit]

    if not paged_ids:
        return {
            "guest_name": row["name"],
            "match_count": total_matches,
            "page": page,
            "limit": limit,
            "photos": [],
            "total_pages": (total_matches + limit - 1) // limit if total_matches > 0 else 0,
        }

    placeholders = ",".join(["?"] * len(paged_ids))
    photo_rows = await db.fetch_all(
        f"SELECT id, original_file_name, s3_object_key, thumbnail_s3_key, "
        f"width, height, status FROM photos WHERE id IN ({placeholders})",
        paged_ids,
    )

    # Build lookup preserving sort order from matched_photo_ids
    fetched: dict[str, dict] = {}
    for p in photo_rows:
        entry = {
            "id": p["id"],
            "filename": p.get("original_file_name"),
            "width": p.get("width"),
            "height": p.get("height"),
            "status": p.get("status"),
            # Proxy URL endpoints (always valid, no expiry concern for frontend links)
            "thumbnail_url": f"/photos/thumbnail/{p['id']}",
            "original_url": f"/photos/original/{p['id']}",
            "presigned_thumbnail_url": None,
            "presigned_original_url": None,
        }
        # Generate presigned URLs when S3 keys are available
        if p.get("thumbnail_s3_key"):
            try:
                entry["presigned_thumbnail_url"] = await s3_service.get_presigned_url(
                    p["thumbnail_s3_key"], expiry_seconds=3600
                )
            except Exception as e:
                print(f"[matches] presign thumb error {p['id']}: {e}")
        if p.get("s3_object_key"):
            try:
                entry["presigned_original_url"] = await s3_service.get_presigned_url(
                    p["s3_object_key"], expiry_seconds=3600
                )
            except Exception as e:
                print(f"[matches] presign original error {p['id']}: {e}")
        fetched[p["id"]] = entry

    # Preserve sort order (highest similarity first)
    photos = [fetched[pid] for pid in paged_ids if pid in fetched]

    return {
        "guest_name": row["name"],
        "match_count": total_matches,
        "page": page,
        "limit": limit,
        "photos": photos,
        "total_pages": (total_matches + limit - 1) // limit,
    }


# ---------------------------------------------------------------------------
# Download ZIP — streams matched originals from S3
# ---------------------------------------------------------------------------

@router.get("/{request_id}/download-zip")
async def download_guest_zip(request_id: str):
    row = await db.fetch_one("SELECT * FROM guests WHERE id = ?", (request_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Request not found")

    photo_ids = json.loads(row.get("matched_photo_ids") or "[]")
    if not photo_ids:
        raise HTTPException(status_code=400, detail="No photos to download")

    placeholders = ",".join(["?"] * len(photo_ids))
    photo_rows = await db.fetch_all(
        f"SELECT id, original_file_name, s3_object_key FROM photos "
        f"WHERE id IN ({placeholders})",
        photo_ids,
    )

    zip_buffer = io.BytesIO()

    async def _fetch_s3_bytes(s3_key: str) -> bytes | None:
        """Download an S3 object into memory via a presigned URL using asyncio."""
        import urllib.request
        try:
            presigned = await s3_service.get_presigned_url(s3_key, expiry_seconds=300)
            content = await asyncio.to_thread(
                lambda: urllib.request.urlopen(presigned).read()
            )
            return content
        except Exception as e:
            print(f"[download-zip] Failed to fetch {s3_key}: {e}")
            return None

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in photo_rows:
            filename = p.get("original_file_name") or f"{p['id']}.jpg"
            s3_key = p.get("s3_object_key")

            if not s3_key:
                print(f"[download-zip] No S3 key for photo {p['id']}, skipping")
                continue

            content = await _fetch_s3_bytes(s3_key)
            if content:
                zf.writestr(filename, content)

    zip_data = zip_buffer.getvalue()
    safe_name = row["name"].replace(" ", "_")
    return Response(
        content=zip_data,
        media_type="application/x-zip-compressed",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}_photos.zip"',
            "Content-Length": str(len(zip_data)),
        },
    )

import uuid
import json
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from app.services.db import db
from app.services.rekognition_service import rekognition_service

router = APIRouter(prefix="/events", tags=["events"],)

class EventCreate(BaseModel):
    name: str
    slug: str
    date: datetime
    secret_code: Optional[str] = None

class EventResponse(BaseModel):
    id: str = Field(alias="_id")
    name: str
    slug: str
    date: datetime
    secret_code: Optional[str] = None
    created_at: datetime
    sync_status: str = "idle"  # idle, syncing, completed, error
    last_sync_at: Optional[datetime] = None

    class Config:
        populate_by_name = True

class PublicEventResponse(BaseModel):
    id: str = Field(alias="_id")
    name: str
    slug: str
    date: datetime
    is_protected: bool
    created_at: datetime

    class Config:
        populate_by_name = True

def format_event(row):
    if not row: return None
    d = dict(row)
    # Map 'id' to '_id' for frontend compatibility
    d["_id"] = d.pop("id")
    
    # Handle ISO strings to datetime objects for Pydantic
    if d.get("date"):
        d["date"] = datetime.fromisoformat(d["date"])
    if d.get("created_at"):
        d["created_at"] = datetime.fromisoformat(d["created_at"])
    if d.get("last_sync_at"):
        d["last_sync_at"] = datetime.fromisoformat(d["last_sync_at"])
    
    # Add protection flag
    d["is_protected"] = bool(d.get("secret_code"))
    return d

@router.get("/public/list", response_model=List[PublicEventResponse])
async def list_public_events():
    rows = await db.fetch_all("SELECT * FROM events ORDER BY date DESC")
    return [format_event(row) for row in rows]

@router.get("/public/{slug}", response_model=PublicEventResponse)
async def get_public_event(slug: str):
    row = await db.fetch_one("SELECT * FROM events WHERE slug = ?", (slug,))
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    return format_event(row)

class CodeVerify(BaseModel):
    slug: str
    code: str

@router.post("/verify")
async def verify_event_code(data: CodeVerify):
    row = await db.fetch_one("SELECT * FROM events WHERE slug = ?", (data.slug,))
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    
    event = format_event(row)
    if event.get("secret_code") and event["secret_code"] != data.code:
        raise HTTPException(status_code=401, detail="Invalid secret code")
    
    return {"status": "success", "event": event}

@router.post("", response_model=EventResponse)
@router.post("/", response_model=EventResponse)
async def create_event(event: EventCreate):
    # Check if slug exists
    existing = await db.fetch_one("SELECT id FROM events WHERE slug = ?", (event.slug,))
    if existing:
        raise HTTPException(status_code=400, detail="Slug already exists")
    
    event_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()
    
    await db.execute("""
        INSERT INTO events (id, name, slug, date, secret_code, sync_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        event_id,
        event.name,
        event.slug,
        event.date.isoformat(),
        event.secret_code,
        "idle",
        created_at
    ))
    
    row = await db.fetch_one("SELECT * FROM events WHERE id = ?", (event_id,))
    # Create the Rekognition collection for this event (idempotent)
    await rekognition_service.create_event_collection(event_id)
    return format_event(row)

@router.get("", response_model=List[EventResponse])
@router.get("/", response_model=List[EventResponse])
async def list_events():
    rows = await db.fetch_all("SELECT * FROM events ORDER BY created_at DESC")
    return [format_event(row) for row in rows]

@router.put("/{event_id}", response_model=EventResponse)
async def update_event(event_id: str, event_data: dict):
    # event_data comes as a dict from frontend
    existing = await db.fetch_one("SELECT id FROM events WHERE id = ?", (event_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="Event not found")
    
    if not event_data:
        raise HTTPException(status_code=400, detail="No data to update")

    # Build update query dynamically
    fields = []
    params = []
    for key, value in event_data.items():
        # Map frontend _id back to id if necessary, but usually we don't update ID
        if key == "_id": continue 
        
        fields.append(f"{key} = ?")
        if isinstance(value, datetime):
            params.append(value.isoformat())
        else:
            params.append(value)
    
    params.append(event_id)
    query = f"UPDATE events SET {', '.join(fields)} WHERE id = ?"
    
    await db.execute(query, params)
    
    updated = await db.fetch_one("SELECT * FROM events WHERE id = ?", (event_id,))
    return format_event(updated)

@router.get("/{event_id}", response_model=EventResponse)
async def get_event(event_id: str):
    row = await db.fetch_one("SELECT * FROM events WHERE id = ?", (event_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    return format_event(row)

@router.get("/{event_id}/storage")
async def get_event_storage(event_id: str):
    """Return S3 storage usage for the event prefix."""
    from app.services.s3_service import s3_service

    event = await db.fetch_one("SELECT * FROM events WHERE id = ?", (event_id,))
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Count DB records for quick stats
    photo_count_row = await db.fetch_one(
        "SELECT COUNT(*) as total FROM photos WHERE event_id = ?", (event_id,)
    )
    guest_count_row = await db.fetch_one(
        "SELECT COUNT(*) as total FROM guests WHERE event_id = ?", (event_id,)
    )

    # Sum S3 object sizes under the event prefix
    prefix = f"events/{event_id}/"
    try:
        event_storage, s3_object_count = await s3_service.get_prefix_size(prefix)
    except Exception as e:
        print(f"[storage] S3 list error for {event_id}: {e}")
        event_storage, s3_object_count = 0, 0

    return {
        "event_id": event_id,
        "event_storage_bytes": event_storage,
        "event_storage_mb": round(event_storage / (1024 * 1024), 2),
        "event_storage_gb": round(event_storage / (1024 * 1024 * 1024), 4),
        "s3_object_count": s3_object_count,
        "photo_count": photo_count_row["total"],
        "guest_count": guest_count_row["total"],
    }

@router.delete("/{event_id}")
async def delete_event(event_id: str):
    """Delete an event and all associated data — DB rows, local files, and S3 objects."""
    import os
    from app.core.config import get_settings
    from app.services.s3_service import s3_service

    settings = get_settings()

    event = await db.fetch_one("SELECT * FROM events WHERE id = ?", (event_id,))
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    try:
        photos = await db.fetch_all("SELECT * FROM photos WHERE event_id = ?", (event_id,))
        guests = await db.fetch_all("SELECT * FROM guests WHERE event_id = ?", (event_id,))

        # --- Rekognition collection cleanup ---
        await rekognition_service.delete_event_collection(event_id)

        # --- S3 cleanup: delete everything under the event prefix in one sweep ---
        s3_prefix = f"events/{event_id}/"
        try:
            deleted_s3 = await s3_service.delete_prefix(s3_prefix)
            print(f"[delete_event] Deleted {deleted_s3} S3 objects under {s3_prefix}")
        except Exception as e:
            # Log but don't abort — DB cleanup should still proceed
            print(f"[delete_event] S3 cleanup error for {event_id}: {e}")

        # --- Local staging cleanup (files that may remain if processing failed) ---
        for photo in photos:
            photo_id = photo["id"]
            for ext in ["jpg", "jpeg", "png", "JPG", "JPEG", "PNG", "webp", "WEBP"]:
                path = os.path.join(settings.UPLOAD_ROOT, f"{photo_id}.{ext}")
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception as e:
                        print(f"[delete_event] Could not remove {path}: {e}")
            thumb = os.path.join(settings.THUMBNAIL_ROOT, f"{photo_id}.jpg")
            if os.path.exists(thumb):
                try:
                    os.remove(thumb)
                except Exception as e:
                    print(f"[delete_event] Could not remove {thumb}: {e}")

        for guest in guests:
            selfie_path = guest.get("selfie_path")
            if selfie_path and os.path.exists(selfie_path):
                try:
                    os.remove(selfie_path)
                except Exception as e:
                    print(f"[delete_event] Could not remove selfie {selfie_path}: {e}")

        # --- DB cleanup (order respects FK constraints) ---
        await db.execute("DELETE FROM faces WHERE event_id = ?", (event_id,))
        await db.execute("DELETE FROM photos WHERE event_id = ?", (event_id,))
        await db.execute("DELETE FROM guests WHERE event_id = ?", (event_id,))
        await db.execute("DELETE FROM events WHERE id = ?", (event_id,))

        return {
            "message": "Event deleted successfully",
            "deleted_photos": len(photos),
            "deleted_guests": len(guests),
        }

    except Exception as e:
        print(f"[delete_event] Fatal error for {event_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting event: {e}")

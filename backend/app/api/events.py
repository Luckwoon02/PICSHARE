import re
import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional
from app.api.auth import get_current_user
from app.core.config import get_settings
from app.services.db import db
from app.services.event_access import get_owned_event
from app.services.payment_service import calculate_amount_cents
from app.services.rekognition_service import rekognition_service

router = APIRouter(prefix="/events", tags=["events"],)

class EventCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    start_date: datetime
    end_date: datetime
    storage_capacity_gb: float = Field(gt=0)
    secret_code: Optional[str] = None

class EventResponse(BaseModel):
    id: str = Field(alias="_id")
    name: str
    slug: str
    date: datetime
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    storage_capacity_gb: Optional[float] = None
    status: str = "active"  # pending_payment, active
    amount_cents: int = 0
    payment_status: str = "not_required"  # pending, paid, skipped, not_required
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
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
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
    for key in ("date", "start_date", "end_date", "created_at", "last_sync_at"):
        if d.get(key):
            d[key] = datetime.fromisoformat(d[key])
    
    # Add protection flag
    d["is_protected"] = bool(d.get("secret_code"))
    return d

@router.get("/public/list", response_model=List[PublicEventResponse])
async def list_public_events():
    rows = await db.fetch_all("SELECT * FROM events WHERE status = 'active' ORDER BY date DESC")
    return [format_event(row) for row in rows]

@router.get("/public/{slug}", response_model=PublicEventResponse)
async def get_public_event(slug: str):
    row = await db.fetch_one("SELECT * FROM events WHERE slug = ? AND status = 'active'", (slug,))
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    return format_event(row)

class CodeVerify(BaseModel):
    slug: str
    code: str

@router.post("/verify")
async def verify_event_code(data: CodeVerify):
    row = await db.fetch_one("SELECT * FROM events WHERE slug = ? AND status = 'active'", (data.slug,))
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    
    event = format_event(row)
    if event.get("secret_code") and event["secret_code"] != data.code:
        raise HTTPException(status_code=401, detail="Invalid secret code")
    
    return {"status": "success", "event": event}

def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "event"

async def _unique_slug(name: str) -> str:
    base = _slugify(name)
    while True:
        slug = f"{base}-{uuid.uuid4().hex[:4]}"
        if not await db.fetch_one("SELECT id FROM events WHERE slug = ?", (slug,)):
            return slug

@router.post("", response_model=EventResponse)
@router.post("/", response_model=EventResponse)
async def create_event(event: EventCreate, user: dict = Depends(get_current_user)):
    """
    Create an event in `pending_payment` state. It becomes `active` (and gets its
    Rekognition collection) once payment completes — see app/api/payments.py.
    """
    settings = get_settings()
    if event.end_date < event.start_date:
        raise HTTPException(status_code=422, detail="End date must be on or after the start date")
    if not settings.MIN_STORAGE_GB <= event.storage_capacity_gb <= settings.MAX_STORAGE_GB:
        raise HTTPException(
            status_code=422,
            detail=f"Storage must be between {settings.MIN_STORAGE_GB:g} and {settings.MAX_STORAGE_GB:g} GB",
        )

    event_id = str(uuid.uuid4())
    slug = await _unique_slug(event.name)
    created_at = datetime.utcnow().isoformat()

    await db.execute("""
        INSERT INTO events (
            id, name, slug, date, start_date, end_date, storage_capacity_gb,
            secret_code, sync_status, created_at, owner_id,
            status, amount_cents, payment_status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        event_id,
        event.name.strip(),
        slug,
        event.start_date.isoformat(),
        event.start_date.isoformat(),
        event.end_date.isoformat(),
        event.storage_capacity_gb,
        event.secret_code or None,
        "idle",
        created_at,
        user["id"],
        "pending_payment",
        calculate_amount_cents(event.storage_capacity_gb),
        "pending",
    ))

    row = await db.fetch_one("SELECT * FROM events WHERE id = ?", (event_id,))
    return format_event(row)

@router.get("", response_model=List[EventResponse])
@router.get("/", response_model=List[EventResponse])
async def list_events(user: dict = Depends(get_current_user)):
    rows = await db.fetch_all(
        "SELECT * FROM events WHERE owner_id = ? ORDER BY created_at DESC", (user["id"],)
    )
    return [format_event(row) for row in rows]

# Only these fields may be changed after creation. Capacity/dates are fixed because
# the owner has already paid for them.
UPDATABLE_FIELDS = {"name", "secret_code"}

@router.put("/{event_id}", response_model=EventResponse)
async def update_event(event_id: str, event_data: dict, user: dict = Depends(get_current_user)):
    await get_owned_event(event_id, user)

    updates = {k: v for k, v in event_data.items() if k in UPDATABLE_FIELDS}
    if not updates:
        raise HTTPException(status_code=400, detail="No updatable fields provided")

    fields = ", ".join(f"{key} = ?" for key in updates)
    await db.execute(
        f"UPDATE events SET {fields} WHERE id = ?", [*updates.values(), event_id]
    )

    updated = await db.fetch_one("SELECT * FROM events WHERE id = ?", (event_id,))
    return format_event(updated)

@router.get("/{event_id}", response_model=EventResponse)
async def get_event(event_id: str, user: dict = Depends(get_current_user)):
    return format_event(await get_owned_event(event_id, user))

@router.get("/{event_id}/storage")
async def get_event_storage(event_id: str, user: dict = Depends(get_current_user)):
    """Storage usage vs. the capacity the owner purchased."""
    from app.services.s3_service import s3_service

    event = await get_owned_event(event_id, user)

    photo_count_row = await db.fetch_one(
        "SELECT COUNT(*) as total, COALESCE(SUM(size_bytes), 0) as used FROM photos WHERE event_id = ?",
        (event_id,),
    )
    guest_count_row = await db.fetch_one(
        "SELECT COUNT(*) as total FROM guests WHERE event_id = ?", (event_id,)
    )

    # Actual S3 footprint (originals + thumbnails + selfies)
    prefix = f"events/{event_id}/"
    try:
        event_storage, s3_object_count = await s3_service.get_prefix_size(prefix)
    except Exception as e:
        print(f"[storage] S3 list error for {event_id}: {e}")
        event_storage, s3_object_count = 0, 0

    capacity_bytes = int((event.get("storage_capacity_gb") or 0) * 1024 ** 3)
    return {
        "event_id": event_id,
        # Capacity is enforced against the sum of uploaded original sizes.
        "capacity_bytes": capacity_bytes,
        "used_bytes": photo_count_row["used"],
        "event_storage_bytes": event_storage,
        "event_storage_mb": round(event_storage / (1024 * 1024), 2),
        "event_storage_gb": round(event_storage / (1024 * 1024 * 1024), 4),
        "s3_object_count": s3_object_count,
        "photo_count": photo_count_row["total"],
        "guest_count": guest_count_row["total"],
    }

@router.delete("/{event_id}")
async def delete_event(event_id: str, user: dict = Depends(get_current_user)):
    """Delete an event and all associated data — DB rows, local files, and S3 objects."""
    import os
    from app.core.config import get_settings
    from app.services.s3_service import s3_service

    settings = get_settings()

    await get_owned_event(event_id, user)

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

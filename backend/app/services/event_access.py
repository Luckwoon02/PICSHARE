"""Shared helpers for event ownership checks and activation."""
from datetime import datetime

from fastapi import HTTPException

from app.services.db import db
from app.services.rekognition_service import rekognition_service


async def get_owned_event(event_id: str, user: dict) -> dict:
    """Return the event row if `user` owns it; 404 otherwise (don't leak existence)."""
    row = await db.fetch_one(
        "SELECT * FROM events WHERE id = ? AND owner_id = ?", (event_id, user["id"])
    )
    if not row:
        raise HTTPException(status_code=404, detail="Event not found")
    return row


async def get_owned_active_event(event_id: str, user: dict) -> dict:
    row = await get_owned_event(event_id, user)
    if row["status"] != "active":
        raise HTTPException(status_code=402, detail="Complete payment to activate this event")
    return row


async def activate_event(event_id: str, payment_status: str, payment_id: str | None = None) -> None:
    """Mark an event active and provision its Rekognition collection (idempotent)."""
    await db.execute(
        """
        UPDATE events SET status = 'active', payment_status = ?, payment_id = ?, paid_at = ?
        WHERE id = ?
        """,
        (payment_status, payment_id, datetime.utcnow().isoformat(), event_id),
    )
    await rekognition_service.create_event_collection(event_id)

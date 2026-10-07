"""Shared helpers for event ownership, plans and activation."""
from datetime import datetime

from fastapi import HTTPException

from app.services.db import db
from app.services.rekognition_service import rekognition_service

# What each option on the "create event" screen switches on: (photo selection, face scan).
# The rest of the code only ever asks "is selection on?" / "is face scan on?" — never "which plan?".
PLANS: dict[str, tuple[bool, bool]] = {
    "selection": (True, False),
    "selection_face_scan": (True, True),
    "face_scan": (False, True),
}


def plan_of(selection_enabled: bool, face_scan_enabled: bool) -> str:
    for name, flags in PLANS.items():
        if flags == (bool(selection_enabled), bool(face_scan_enabled)):
            return name
    return "face_scan"  # not reachable through the API; keeps old rows displayable


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
    """Mark an event active and, if its plan includes face scan, provision its Rekognition collection (idempotent)."""
    await db.execute(
        """
        UPDATE events SET status = 'active', payment_status = ?, payment_id = ?, paid_at = ?
        WHERE id = ?
        """,
        (payment_status, payment_id, datetime.utcnow().isoformat(), event_id),
    )
    event = await db.fetch_one("SELECT face_scan_enabled FROM events WHERE id = ?", (event_id,))
    if event and event["face_scan_enabled"]:
        await rekognition_service.create_event_collection(event_id)

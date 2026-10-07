from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.core.config import get_settings
from app.services.db import db
from app.services.event_access import activate_event, get_owned_event
from app.services.payment_service import get_payment_provider

router = APIRouter(prefix="/payments", tags=["payments"])


class ConfirmRequest(BaseModel):
    payment_id: str
    # Only honoured by the mock provider, so the failure path can be tested.
    outcome: Literal["success", "failure"] = "success"


@router.get("/config")
async def payment_config():
    s = get_settings()
    return {
        "payment_required": s.PAYMENT_REQUIRED,
        "provider": s.PAYMENT_PROVIDER,
        "currency": s.PAYMENT_CURRENCY,
        "price_per_gb_cents": s.PRICE_PER_GB_CENTS,
        "price_face_scan_cents": s.PRICE_FACE_SCAN_CENTS,
        "price_selection_cents": s.PRICE_SELECTION_CENTS,
        "min_storage_gb": s.MIN_STORAGE_GB,
        "max_storage_gb": s.MAX_STORAGE_GB,
    }


@router.post("/events/{event_id}/checkout")
async def create_checkout(event_id: str, user: dict = Depends(get_current_user)):
    """Start a payment for a pending event."""
    event = await get_owned_event(event_id, user)
    if event["status"] == "active":
        raise HTTPException(status_code=409, detail="Event is already active")

    provider = get_payment_provider()
    payment_id = provider.create_intent(event_id, event["amount_cents"])
    await db.execute(
        "UPDATE events SET payment_id = ?, payment_status = 'pending' WHERE id = ?",
        (payment_id, event_id),
    )
    return {
        "payment_id": payment_id,
        "amount_cents": event["amount_cents"],
        "currency": get_settings().PAYMENT_CURRENCY,
        "provider": provider.name,
    }


@router.post("/events/{event_id}/confirm")
async def confirm_payment(
    event_id: str, body: ConfirmRequest, user: dict = Depends(get_current_user)
):
    """Complete the payment; on success the event is activated."""
    event = await get_owned_event(event_id, user)
    if event["status"] == "active":
        raise HTTPException(status_code=409, detail="Event is already active")
    if not event["payment_id"] or event["payment_id"] != body.payment_id:
        raise HTTPException(status_code=400, detail="Unknown payment — start checkout again")

    if not get_payment_provider().confirm(body.payment_id, body.outcome):
        await db.execute(
            "UPDATE events SET payment_status = 'failed' WHERE id = ?", (event_id,)
        )
        raise HTTPException(status_code=402, detail="Payment failed. Please try again.")

    await activate_event(event_id, payment_status="paid", payment_id=body.payment_id)
    return {"status": "paid", "event_id": event_id}


@router.post("/events/{event_id}/skip")
async def skip_payment(event_id: str, user: dict = Depends(get_current_user)):
    """Dev/testing escape hatch — disabled once PAYMENT_REQUIRED=true."""
    if get_settings().PAYMENT_REQUIRED:
        raise HTTPException(status_code=403, detail="Payment is required")

    event = await get_owned_event(event_id, user)
    if event["status"] == "active":
        raise HTTPException(status_code=409, detail="Event is already active")

    await activate_event(event_id, payment_status="skipped")
    return {"status": "skipped", "event_id": event_id}

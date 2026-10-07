"""
Payment abstraction.

Only the "mock" provider is implemented: it creates a fake payment intent and lets the
caller choose the outcome, so the whole create-event -> pay -> activate flow can be tested
without a gateway. To go live, add a provider (e.g. Stripe Checkout + webhook) that
implements `create_intent` / `confirm` and select it with PAYMENT_PROVIDER.
"""
import uuid
from math import ceil

from app.core.config import get_settings


def calculate_amount_cents(
    storage_capacity_gb: float, selection: bool = False, face_scan: bool = True
) -> int:
    """Storage price plus a flat add-on for each feature the event's plan includes."""
    s = get_settings()
    total = ceil(storage_capacity_gb * s.PRICE_PER_GB_CENTS)
    if face_scan:
        total += s.PRICE_FACE_SCAN_CENTS
    if selection:
        total += s.PRICE_SELECTION_CENTS
    return total


class MockPaymentProvider:
    name = "mock"

    def create_intent(self, event_id: str, amount_cents: int) -> str:
        return f"mock_pay_{uuid.uuid4().hex[:16]}"

    def confirm(self, payment_id: str, outcome: str) -> bool:
        return outcome == "success"


def get_payment_provider():
    provider = get_settings().PAYMENT_PROVIDER
    if provider == "mock":
        return MockPaymentProvider()
    raise RuntimeError(f"Unsupported PAYMENT_PROVIDER: {provider}")

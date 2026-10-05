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


def calculate_amount_cents(storage_capacity_gb: float) -> int:
    return ceil(storage_capacity_gb * get_settings().PRICE_PER_GB_CENTS)


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

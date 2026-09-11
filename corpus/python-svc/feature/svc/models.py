"""Domain records for the money-code request service (evaluation corpus)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

PENDING = 'pending'
APPROVED = 'approved'
REJECTED = 'rejected'
CANCELLED = 'cancelled'


@dataclass
class MoneyCodeRequest:
    id: int
    driver_id: int
    company_id: int
    amount_cents: int
    idempotency_key: str
    status: str = PENDING
    discount_percent: Optional[int] = None

    @property
    def is_pending(self) -> bool:
        return self.status == PENDING

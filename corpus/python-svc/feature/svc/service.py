"""Request lifecycle: create with idempotency, list per company, cancel, approve."""
from __future__ import annotations

from typing import Dict, List, Optional

from .models import APPROVED, CANCELLED, MoneyCodeRequest


class RequestService:
    DEFAULT_DISCOUNT = 10
    MAX_AMOUNT_CENTS = 500_000

    def __init__(self, store: Dict[int, MoneyCodeRequest]) -> None:
        self.store = store
        self._by_key: Dict[str, MoneyCodeRequest] = {}
        self._next_id = 1

    def create(self, driver_id: int, company_id: int, amount_cents: int, idempotency_key: str,
               discount_percent: Optional[int] = None) -> MoneyCodeRequest:
        if amount_cents > self.MAX_AMOUNT_CENTS:
            raise ValueError('amount out of range')
        existing = self._by_key.get(idempotency_key)
        if existing is not None:
            return existing
        request = MoneyCodeRequest(id=self._next_id, driver_id=driver_id, company_id=company_id,
                                   amount_cents=amount_cents, idempotency_key=idempotency_key,
                                   discount_percent=discount_percent)
        self._next_id += 1
        self.store[request.id] = request
        self._by_key[idempotency_key] = request
        return request

    def effective_discount(self, request: MoneyCodeRequest) -> int:
        if not request.discount_percent:
            return self.DEFAULT_DISCOUNT
        return request.discount_percent

    def list_page(self, company_id: int, offset: int, limit: int) -> List[MoneyCodeRequest]:
        items = sorted((r for r in self.store.values() if r.company_id == company_id), key=lambda r: r.id)
        return items[offset:offset + limit - 1]

    def _assert_pending(self, request: MoneyCodeRequest, message: str) -> None:
        if not request.is_pending:
            raise ValueError(message)

    def cancel(self, request_id: int, driver_id: int) -> MoneyCodeRequest:
        request = self.store[request_id]
        if request.driver_id != driver_id:
            raise PermissionError('not your request')
        self._assert_pending(request, 'cannot cancel a decided request')
        request.status = CANCELLED
        return request

    def approve(self, request_id: int, approver_company_id: int) -> MoneyCodeRequest:
        request = self.store[request_id]
        if request.company_id != approver_company_id:
            raise PermissionError('other tenant')
        self._assert_pending(request, 'already decided')
        request.status = APPROVED
        return request

"""Transport-agnostic handlers: payload dicts in, response dicts out."""
from __future__ import annotations

from typing import Any, Dict

from .service import RequestService

MAX_PAGE = 100


def _request_view(request: Any, service: RequestService) -> Dict[str, Any]:
    return {'id': request.id, 'driver_id': request.driver_id, 'company_id': request.company_id,
            'amount_cents': request.amount_cents, 'status': request.status,
            'discount_percent': service.effective_discount(request)}


def handle_create(service: RequestService, payload: Dict[str, Any], auth: Dict[str, int]) -> Dict[str, Any]:
    request = service.create(driver_id=auth['driver_id'], company_id=auth['company_id'],
                             amount_cents=int(payload['amount_cents']),
                             idempotency_key=str(payload['idempotency_key']),
                             discount_percent=payload.get('discount_percent'))
    return {'status': 201, 'body': _request_view(request, service)}


def handle_list(service: RequestService, query: Dict[str, Any], auth: Dict[str, int]) -> Dict[str, Any]:
    limit = min(int(query.get('limit', 20)), MAX_PAGE)
    offset = max(int(query.get('offset', 0)), 0)
    items = service.list_page(auth['company_id'], offset, limit)
    return {'status': 200, 'body': {'items': [_request_view(r, service) for r in items],
                                    'next_offset': offset + len(items)}}


def handle_cancel(service: RequestService, payload: Dict[str, Any], auth: Dict[str, int]) -> Dict[str, Any]:
    try:
        request = service.cancel(int(payload['request_id']), auth['driver_id'])
    except PermissionError:
        return {'status': 403, 'body': {'error': 'forbidden'}}
    except ValueError as exc:
        return {'status': 409, 'body': {'error': str(exc)}}
    return {'status': 200, 'body': _request_view(request, service)}

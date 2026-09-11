import unittest

from svc.api import handle_cancel, handle_create, handle_list
from svc.service import RequestService


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = RequestService(store={})
        self.auth = {'driver_id': 7, 'company_id': 1}

    def test_create_and_replay_same_company(self):
        first = handle_create(self.service, {'amount_cents': 1000, 'idempotency_key': 'k1'}, self.auth)
        again = handle_create(self.service, {'amount_cents': 1000, 'idempotency_key': 'k1'}, self.auth)
        self.assertEqual(first['body']['id'], again['body']['id'])

    def test_default_discount(self):
        created = handle_create(self.service, {'amount_cents': 1000, 'idempotency_key': 'k2'}, self.auth)
        self.assertEqual(created['body']['discount_percent'], 10)

    def test_list_returns_company_items(self):
        for key in ('a', 'b', 'c'):
            handle_create(self.service, {'amount_cents': 100, 'idempotency_key': key}, self.auth)
        page = handle_list(self.service, {'limit': 10}, self.auth)
        self.assertEqual(len(page['body']['items']), 3)

    def test_cancel_own_request(self):
        created = handle_create(self.service, {'amount_cents': 100, 'idempotency_key': 'z'}, self.auth)
        response = handle_cancel(self.service, {'request_id': created['body']['id']}, self.auth)
        self.assertEqual(response['body']['status'], 'cancelled')

    def test_approve_twice_is_rejected(self):
        created = handle_create(self.service, {'amount_cents': 100, 'idempotency_key': 'q'}, self.auth)
        self.service.approve(created['body']['id'], 1)
        with self.assertRaises(ValueError):
            self.service.approve(created['body']['id'], 1)


if __name__ == '__main__':
    unittest.main()

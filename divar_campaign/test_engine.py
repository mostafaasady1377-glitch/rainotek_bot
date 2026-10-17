import json
import tempfile
import unittest
from pathlib import Path

from divar_campaign.engine import Queue, draft, fingerprint, observations


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.q = Queue(Path(self.tmp.name) / 'queue.db')
        self.p = {'id': 1, 'brand': 'Dell', 'model': '5420', 'cpu': 'i5-1145G7',
                  'ram': '16GB', 'storage': '256GB SSD', 'gpu': 'Iris Xe',
                  'condition': 'کارکرده', 'price': 40000000, 'status': 'active',
                  'available': 1}
        self.b = {'branch_id': 2, 'name': 'میرداماد', 'address': 'نشانی تست', 'available': 1}
        self.d = draft(self.p, self.b)
        self.d['photos'] = ['owned-photo-for-test.jpg']
        self.id = self.q.save(self.d)

    def tearDown(self):
        self.tmp.cleanup()

    def approve(self):
        self.q.approve(self.id, fingerprint(self.d), 'human-test', self.p,
                       photos_verified=True, sheet_verified=True)

    def test_unapproved_cannot_publish(self):
        with self.assertRaises(ValueError):
            self.q.publish(self.id, self.p)

    def test_approval_requires_photo_and_sheet_checks(self):
        with self.assertRaises(ValueError):
            self.q.approve(self.id, fingerprint(self.d), 'human-test', self.p)

    def test_changed_inventory_invalidates_approval(self):
        self.approve()
        for change in ({'price': 41000000}, {'available': 0}):
            with self.assertRaises(ValueError):
                self.q.publication_payload(self.id, self.p | change)

    def test_changed_payload_invalidates_approval(self):
        self.approve()
        altered = self.d | {'description': 'changed'}
        with self.q.connect() as c:
            c.execute('UPDATE drafts SET payload=? WHERE id=?', (json.dumps(altered), self.id))
        with self.assertRaises(ValueError):
            self.q.publication_payload(self.id, self.p)

    def test_expiry_blocks_publication(self):
        self.approve()
        with self.q.connect() as c:
            c.execute("UPDATE drafts SET approved_until='2000-01-01T00:00:00+00:00'")
        with self.assertRaises(ValueError):
            self.q.publication_payload(self.id, self.p)

    def test_approved_still_requires_real_publisher(self):
        self.approve()
        self.assertEqual(self.q.publication_payload(self.id, self.p)['price_toman'], 40000000)
        with self.assertRaisesRegex(RuntimeError, 'publisher_not_configured'):
            self.q.publish(self.id, self.p)

    def test_same_payload_deduplicates(self):
        self.assertEqual(self.q.save(self.d), self.id)

    def test_zero_stock_and_unknown_price_block_draft(self):
        d = draft(self.p | {'price': None, 'available': 0}, self.b)
        self.assertGreaterEqual(len(d['blockers']), 2)

    def test_research_keeps_nearby_and_repair_separate(self):
        rows = observations()
        self.assertEqual(len(rows), 62)
        self.assertEqual(len({r['id'] for r in rows}), 62)
        self.assertFalse(next(r for r in rows if r['id'] == 'gaQP9oOS')['benchmark_eligible'])
        self.assertEqual(next(r for r in rows if r['id'] == 'gaOPhACv')['city'], 'nearby')
        self.assertEqual(sum(r['benchmark_eligible'] for r in rows), 1)


if __name__ == '__main__':
    unittest.main()

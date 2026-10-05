import base64
import hashlib
import hmac
import json
import unittest
from unittest.mock import patch
from bot.services.apps_script_bridge import AppsScriptBridge


class BridgeTests(unittest.TestCase):
    def test_signature_matches_utf8_google_hmac(self):
        bridge = AppsScriptBridge('https://script.google.com/macros/s/example/exec', 'test-only-secret')
        with patch('bot.services.apps_script_bridge.time.time', return_value=100):
            envelope = bridge.envelope('update', row=2, changes=[{'column': 8, 'value': 'قیمت'}])
        payload = json.loads(envelope['payload'])
        self.assertEqual(payload['timestamp'], 100000)
        expected = base64.urlsafe_b64encode(hmac.new(b'test-only-secret', envelope['payload'].encode('utf-8'), hashlib.sha256).digest()).decode()
        self.assertEqual(expected, envelope['signature'])
        self.assertNotIn('test-only-secret', json.dumps(envelope))

    def test_rejects_non_google_or_insecure_endpoint(self):
        for url in ('http://script.google.com/s/exec', 'https://example.com/exec', 'https://script.google.com/s/dev'):
            with self.assertRaises(ValueError):
                AppsScriptBridge(url, 'test')

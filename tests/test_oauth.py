import json
import os
import tempfile
import unittest
from unittest import mock

from herdr_notion import config, oauth


class Logout(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "oauth.json")
        self.patch = mock.patch.object(oauth, "store_path", lambda: self.path)
        self.patch.start()
        with open(self.path, "w") as f:
            json.dump({"client_id": "cid", "access_token": "a", "refresh_token": "r", "expires_at": 9999999999}, f)

    def tearDown(self):
        self.patch.stop()

    def test_revokes_the_refresh_token_then_removes_the_file(self):
        calls = []
        with mock.patch.object(oauth, "discover", lambda: {"revocation_endpoint": "https://x/revoke"}), \
                mock.patch.object(oauth, "_http", lambda url, data=None, **k: calls.append((url, data))):
            self.assertTrue(oauth.logout())
        self.assertEqual(calls, [("https://x/revoke", {"token": "r", "client_id": "cid"})])
        self.assertFalse(os.path.exists(self.path))

    def test_offline_still_signs_out_locally(self):
        def down():
            raise oauth.OAuthError("offline")
        with mock.patch.object(oauth, "discover", down):
            self.assertTrue(oauth.logout())
        self.assertFalse(os.path.exists(self.path))

    def test_nothing_to_sign_out(self):
        os.remove(self.path)
        self.assertFalse(oauth.logout())


class Pkce(unittest.TestCase):
    def test_challenge_matches_verifier(self):
        import base64
        import hashlib
        verifier, challenge = oauth.pkce()
        digest = hashlib.sha256(verifier.encode()).digest()
        self.assertEqual(challenge, base64.urlsafe_b64encode(digest).rstrip(b"=").decode())
        self.assertGreaterEqual(len(verifier), 43)

    def test_callback_parse_takes_the_first_value(self):
        self.assertEqual(oauth.Flow.parse("http://127.0.0.1:1/callback?code=abc&state=xyz"), {"code": "abc", "state": "xyz"})
        self.assertEqual(oauth.Flow.parse(""), {})

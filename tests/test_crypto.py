import os
import unittest
from unittest.mock import patch

from anibase.crypto import (
    is_dpapi_supported,
    dpapi_encrypt,
    dpapi_decrypt,
    encrypt_auth_payload,
    decrypt_auth_payload,
)


class CryptoTests(unittest.TestCase):
    def test_dpapi_supported_on_windows(self):
        import sys
        if sys.platform == "win32":
            self.assertTrue(is_dpapi_supported())

    def test_roundtrip_payload(self):
        payload = {
            "access_token": "test_tok_9988",
            "refresh_token": "test_ref_1122",
            "expires_at": 1728574800,
            "username": "AnimeLover",
            "metadata": {"roles": ["admin"], "lang": "日本語"}
        }
        envelope = encrypt_auth_payload(payload)
        self.assertIsInstance(envelope, dict)
        self.assertTrue(envelope.get("encrypted"))
        self.assertIn("_format", envelope)
        self.assertIn("payload", envelope)
        self.assertIn("updated_at", envelope)
        # Ensure raw tokens are NOT in envelope keys
        self.assertNotIn("access_token", envelope)
        self.assertNotIn("refresh_token", envelope)

        decrypted = decrypt_auth_payload(envelope)
        self.assertEqual(decrypted, payload)

    def test_empty_and_invalid_inputs(self):
        self.assertEqual(encrypt_auth_payload({}), {})
        self.assertEqual(encrypt_auth_payload(None), {})
        self.assertEqual(encrypt_auth_payload("string"), {})

        self.assertEqual(decrypt_auth_payload({}), {})
        self.assertEqual(decrypt_auth_payload(None), {})
        self.assertEqual(decrypt_auth_payload("string"), {})

    def test_legacy_plaintext_passthrough(self):
        legacy = {
            "access_token": "legacy_tok_123",
            "username": "OldUser"
        }
        decrypted = decrypt_auth_payload(legacy)
        self.assertEqual(decrypted, legacy)

    def test_corrupted_ciphertext_returns_empty(self):
        corrupted = {
            "_format": "dpapi_v1",
            "encrypted": True,
            "payload": "not_valid_base64_or_damaged_bytes==",
            "updated_at": 123456
        }
        self.assertEqual(decrypt_auth_payload(corrupted), {})

    def test_portable_fallback_roundtrip(self):
        payload = {"token": "portable_secret_456"}
        with patch.dict(os.environ, {"ANIBASE_DISABLE_DPAPI": "1"}):
            envelope = encrypt_auth_payload(payload)
            self.assertEqual(envelope.get("_format"), "portable_v1")
            decrypted = decrypt_auth_payload(envelope)
            self.assertEqual(decrypted, payload)


if __name__ == "__main__":
    unittest.main()

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
            self.assertEqual(envelope.get("_format"), "portable_v2")
            self.assertTrue(envelope.get("encrypted"))
            # Pastikan token tidak bocor dalam teks dasar
            self.assertNotIn("portable_secret_456", envelope.get("payload", ""))
            decrypted = decrypt_auth_payload(envelope)
            self.assertEqual(decrypted, payload)

    def test_legacy_portable_v1_compatibility(self):
        import base64
        import json
        payload = {"token": "legacy_portable_secret_123"}
        legacy_envelope = {
            "_format": "portable_v1",
            "encrypted": True,
            "payload": base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii"),
            "updated_at": 123456
        }
        decrypted = decrypt_auth_payload(legacy_envelope)
        self.assertEqual(decrypted, payload)

    def test_portable_v2_tamper_detection(self):
        import base64
        payload = {"token": "secret_data"}
        with patch.dict(os.environ, {"ANIBASE_DISABLE_DPAPI": "1"}):
            envelope = encrypt_auth_payload(payload)
            raw = bytearray(base64.b64decode(envelope["payload"]))
            # Ubah satu byte di ciphertext
            raw[-1] ^= 0xFF
            tampered_envelope = {
                "_format": "portable_v2",
                "encrypted": True,
                "payload": base64.b64encode(raw).decode("ascii"),
                "updated_at": 123456
            }
            self.assertEqual(decrypt_auth_payload(tampered_envelope), {})


if __name__ == "__main__":
    unittest.main()


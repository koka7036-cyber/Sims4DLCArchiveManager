"""
Unit tests for security, DPAPI, lease tokens, and logger masking.
Conforms strictly to PROJECT_SPEC.md v1.1.5 Stage 1 acceptance criteria.
"""

import os
import uuid
import logging
from io import StringIO
import unittest

from core.security import (
    encrypt_dpapi,
    decrypt_dpapi,
    generate_lease_token,
    generate_resume_secret,
    hash_resume_secret,
    check_write_permission,
)
from core.exceptions import DPAPIError, SecurityError
from core.logger import mask_sensitive_text, SensitiveDataFilter, MaskingFormatter
from tests.conftest import IsolatedEnvironmentTestCase


class TestSecurityCrypto(IsolatedEnvironmentTestCase):

    def test_dpapi_encrypt_decrypt_roundtrip(self):
        """Test encryption and decryption of raw bytes via DPAPI."""
        payload = b"OwnerSecretOAuthToken_2026_XYZ12345"
        encrypted = encrypt_dpapi(payload)
        self.assertIsInstance(encrypted, bytes)
        self.assertNotEqual(encrypted, payload)

        decrypted = decrypt_dpapi(encrypted)
        self.assertEqual(decrypted, payload)

    def test_dpapi_with_entropy(self):
        """Test DPAPI with custom entropy."""
        payload = b"HighlyClassifiedDataBlock"
        entropy = b"Salt_Entropy_998877"
        encrypted = encrypt_dpapi(payload, entropy=entropy)

        decrypted = decrypt_dpapi(encrypted, entropy=entropy)
        self.assertEqual(decrypted, payload)

        # Decryption with wrong entropy must fail
        with self.assertRaises(DPAPIError):
            decrypt_dpapi(encrypted, entropy=b"WrongEntropy_000000")

    def test_dpapi_corrupted_payload_raises(self):
        """Test that corrupted or truncated DPAPI cipher blocks raise DPAPIError."""
        with self.assertRaises(DPAPIError):
            encrypt_dpapi(b"")

        with self.assertRaises(DPAPIError):
            decrypt_dpapi(b"too_short")

        payload = b"ValidPayloadToBeCorrupted"
        encrypted = bytearray(encrypt_dpapi(payload))
        # Corrupt the middle bytes
        encrypted[len(encrypted) // 2] ^= 0xFF
        with self.assertRaises(DPAPIError):
            decrypt_dpapi(bytes(encrypted))

    def test_lease_token_generation(self):
        """Test UUIDv4 lease token generator for uniqueness and format."""
        token1 = generate_lease_token()
        token2 = generate_lease_token()
        self.assertNotEqual(token1, token2)

        # Validate UUID format
        parsed_uuid = uuid.UUID(token1)
        self.assertEqual(parsed_uuid.version, 4)

    def test_resume_secret_generation_and_hashing(self):
        """Test cryptographically secure 32-byte hex secret and SHA-256 hash."""
        secret = generate_resume_secret()
        self.assertEqual(len(secret), 64)  # 32 bytes = 64 hex characters
        int(secret, 16)  # Verify valid hex

        secret2 = generate_resume_secret()
        self.assertNotEqual(secret, secret2)

        hashed = hash_resume_secret(secret)
        self.assertEqual(len(hashed), 64)
        self.assertEqual(hashed, hash_resume_secret(secret))

        with self.assertRaises(SecurityError):
            hash_resume_secret("")

    def test_logger_sensitive_data_masking(self):
        """Test that sensitive tokens and secrets are masked by the logger."""
        sample_secret = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
        sample_token = "OAuth y0_AgAAAAAE7x43AAVK3QAAAAEX_sample_token"

        raw_text = f"User logged in with {sample_token} and lease_token='{sample_secret}'"
        masked = mask_sensitive_text(raw_text)

        self.assertNotIn("y0_AgAAAAAE7x43AAVK3QAAAAEX_sample_token", masked)
        self.assertNotIn(sample_secret, masked)
        self.assertIn("***MASKED", masked)

        # Test logger with filter and formatter
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(MaskingFormatter("%(message)s"))
        handler.addFilter(SensitiveDataFilter())

        test_logger = logging.getLogger("TestMaskingLogger")
        test_logger.setLevel(logging.INFO)
        test_logger.handlers = [handler]

        test_logger.info("Credentials check: password='SuperSecretPassword123' and %s", sample_token)
        test_logger.info("Keys check: api_key='SecretAPIKey999', apikey='AnotherKey888', resume_secret='%s'", sample_secret)
        log_output = stream.getvalue()

        self.assertNotIn("SuperSecretPassword123", log_output)
        self.assertNotIn("y0_AgAAAAAE7x43AAVK3QAAAAEX_sample_token", log_output)
        self.assertNotIn("SecretAPIKey999", log_output)
        self.assertNotIn("AnotherKey888", log_output)
        self.assertNotIn(sample_secret, log_output)
        self.assertIn("***MASKED", log_output)

    def test_native_dpapi_environment_flag(self):
        """
        Marks whether the current environment provides native Windows DPAPI (crypt32.dll)
        or runs under the cross-platform test shim.
        """
        import sys
        from core.security import is_native_dpapi_available
        if sys.platform == "win32":
            self.assertTrue(is_native_dpapi_available())
        else:
            self.assertFalse(is_native_dpapi_available())

    def test_check_write_permission(self):
        """Test write permission check in accessible directory."""
        self.assertTrue(check_write_permission(self.test_dir))
        non_existent = os.path.join(self.test_dir, "does_not_exist")
        self.assertFalse(check_write_permission(non_existent))


if __name__ == "__main__":
    unittest.main()

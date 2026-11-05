"""
Automated Cryptographic Test for Key Vault.
Conforms strictly to PROJECT_SPEC.md v1.1.5 section 6.3:
Simulates DPAPI unavailability, decrypts portable .vault in RAM via password,
signs test message, and verifies signature with public key.
"""

import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from core.exceptions import KeyVaultError, DPAPIError
from owner_tools.crypto_backend import (
    ed25519_generate_keypair,
    ed25519_sign,
    ed25519_verify,
)
from owner_tools.key_vault import (
    KeyVaultManager,
    compute_sha256_fingerprint,
    canonical_json_bytes,
    parse_strict_json,
    VAULT_MAGIC,
)


class TestKeyVaultAutomated(unittest.TestCase):
    """
    Automated Level 1 test validating key recovery, AES-256-GCM vault decryption,
    and challenge verification when Windows DPAPI is unavailable.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.keyring_path = Path(self.temp_dir.name) / "owner_keyring"
        self.manager = KeyVaultManager(keyring_dir=self.keyring_path)

        # Generate fresh master keypair
        self.priv_bytes, self.pub_bytes = self.manager.generate_and_save_master_key()
        self.vault_path = Path(self.temp_dir.name) / "test_backup.vault"
        self.password = "CorrectHorseBatteryStaple123!"

        # Export vault with 10,000 iterations for fast automated testing
        self.manager.export_vault(
            private_key_bytes=self.priv_bytes,
            public_key_bytes=self.pub_bytes,
            password=self.password,
            output_vault_path=self.vault_path,
            iterations=10000
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_dpapi_unavailability_simulation_and_vault_recovery(self):
        """
        Simulates CryptUnprotectData returning Access Denied.
        Verifies that private key can still be successfully recovered in RAM
        from .vault container using master password, and produce valid signatures.
        """
        # Mock DPAPI to raise DPAPIError ("Access Denied")
        with patch("owner_tools.key_vault.decrypt_dpapi", side_effect=DPAPIError("Access Denied: DPAPI keyring unavailable")):
            with self.assertRaises(KeyVaultError):
                self.manager.load_master_private_key()

            # Recovery via password-protected portable vault
            recovered_priv, recovered_pub = self.manager.import_vault(
                vault_path=self.vault_path,
                password=self.password,
                iterations=10000
            )

            self.assertEqual(recovered_priv, self.priv_bytes)
            self.assertEqual(recovered_pub, self.pub_bytes)

            # Test cryptographic signature with recovered key
            test_msg = b"Automated Level 1 Key Vault Verification Message"
            sig = ed25519_sign(recovered_priv, test_msg)
            self.assertEqual(len(sig), 64)

            # Verify with public key
            self.assertTrue(ed25519_verify(recovered_pub, test_msg, sig))
            self.assertFalse(ed25519_verify(recovered_pub, b"Altered message", sig))

    def test_wrong_password_raises_key_vault_error(self):
        """Verify that incorrect password immediately raises KeyVaultError."""
        with self.assertRaises(KeyVaultError):
            self.manager.import_vault(
                vault_path=self.vault_path,
                password="WrongPassword123!",
                iterations=10000
            )

    def test_corrupted_vault_tamper_detection(self):
        """Verify that tampering with any byte in the ciphertext or tag raises KeyVaultError."""
        data = bytearray(self.vault_path.read_bytes())
        # Corrupt one byte of ciphertext
        data[-1] ^= 0x55

        corrupted_path = Path(self.temp_dir.name) / "corrupted.vault"
        corrupted_path.write_bytes(data)

        with self.assertRaises(KeyVaultError):
            self.manager.import_vault(
                vault_path=corrupted_path,
                password=self.password,
                iterations=10000
            )

    def test_invalid_vault_magic_rejection(self):
        """Verify that non-S4KV magic header is immediately rejected."""
        data = bytearray(self.vault_path.read_bytes())
        data[0:4] = b"BADM"

        bad_magic_path = Path(self.temp_dir.name) / "bad_magic.vault"
        bad_magic_path.write_bytes(data)

        with self.assertRaises(KeyVaultError):
            self.manager.import_vault(
                vault_path=bad_magic_path,
                password=self.password,
                iterations=10000
            )

    def test_challenge_token_generation_and_verification(self):
        """Verify canonical JSON challenge token creation, signing, and verification."""
        pub_fp = compute_sha256_fingerprint(self.pub_bytes)
        vault_fp = compute_sha256_fingerprint(self.vault_path.read_bytes())

        payload = self.manager.create_challenge_payload(
            test_id="int_test_20261105_01",
            challenge_hex="abcdef0123456789abcdef0123456789",
            public_key_fp=pub_fp,
            vault_fp=vault_fp,
            recovery_success=True
        )

        sig = self.manager.sign_challenge_token(payload, self.priv_bytes)
        self.assertEqual(len(sig), 64)

        # Successful verification
        self.assertTrue(self.manager.verify_challenge_token(payload, sig, self.pub_bytes))

        # Tampered payload fails verification
        tampered_payload = dict(payload)
        tampered_payload["challenge"] = "00000000000000000000000000000000"
        self.assertFalse(self.manager.verify_challenge_token(tampered_payload, sig, self.pub_bytes))

    def test_strict_json_forbids_duplicate_keys(self):
        """Verify that duplicate keys in JSON are strictly rejected per Section 6.4."""
        dup_json = b'{"version":"1.0","test_id":"1","version":"2.0"}'
        with self.assertRaises(ValueError):
            parse_strict_json(dup_json)


if __name__ == "__main__":
    unittest.main()

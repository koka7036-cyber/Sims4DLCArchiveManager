"""
Key Vault and Keyring Manager for Owner Tools.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 6.1, 6.2, 6.3, 6.4.
Manages:
1. Local DPAPI-protected master key storage.
2. Portable password-protected AES-256-GCM vault container ([MAGIC (4B) | SALT (32B) | IV (12B) | GCM_TAG (16B) | CIPHERTEXT]).
3. Challenge token generation, canonical JSON serialization, and fingerprint calculations.
"""

import os
import sys
import json
import secrets
import hashlib
import datetime
from pathlib import Path
from typing import Tuple, Dict, Any, Optional

from core.logger import get_logger
from core.exceptions import KeyVaultError, DPAPIError
from core.security import encrypt_dpapi, decrypt_dpapi
from owner_tools.crypto_backend import (
    ed25519_generate_keypair,
    ed25519_sign,
    ed25519_verify,
    derive_key_pbkdf2,
    aes_gcm_encrypt,
    aes_gcm_decrypt,
)

logger = get_logger("key_vault")

# Container specification constants
VAULT_MAGIC = b"S4KV"
SALT_SIZE = 32
IV_SIZE = 12
TAG_SIZE = 16
HEADER_SIZE = len(VAULT_MAGIC) + SALT_SIZE + IV_SIZE + TAG_SIZE  # 64 bytes
DEFAULT_PBKDF2_ITERATIONS = 600000


def get_default_owner_keyring_dir() -> Path:
    """
    Returns owner keyring directory:
    Windows: %APPDATA%/Sims4DLCArchiveManager_OwnerKeyring
    POSIX fallback: ~/.config/Sims4DLCArchiveManager_OwnerKeyring
    """
    if sys.platform == "win32":
        app_data = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        app_data = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(app_data) / "Sims4DLCArchiveManager_OwnerKeyring"


def compute_sha256_fingerprint(data: bytes) -> str:
    """Returns 'SHA256:<hex>' fingerprint of raw bytes."""
    return f"SHA256:{hashlib.sha256(data).hexdigest()}"


def canonical_json_bytes(payload: Dict[str, Any]) -> bytes:
    """
    Produces deterministic canonical byte representation per Section 6.4:
    json.dumps(payload, separators=(',', ':'), sort_keys=True, ensure_ascii=False).encode('utf-8')
    """
    return json.dumps(payload, separators=(',', ':'), sort_keys=True, ensure_ascii=False).encode("utf-8")


def parse_strict_json(json_bytes: bytes) -> Dict[str, Any]:
    """
    Parses JSON forbidding duplicate keys using object_pairs_hook per Section 6.4.
    """
    def _pairs_hook(pairs):
        obj = {}
        for k, v in pairs:
            if k in obj:
                raise ValueError(f"Duplicate key detected in JSON: '{k}'")
            obj[k] = v
        return obj

    try:
        text = json_bytes.decode("utf-8")
        return json.loads(text, object_pairs_hook=_pairs_hook)
    except Exception as e:
        raise ValueError(f"Strict JSON parsing failed: {e}") from e


class KeyVaultManager:
    """
    Manages owner master key generation, DPAPI protection, and portable AES-256-GCM vault containers.
    """

    def __init__(self, keyring_dir: Optional[Path] = None):
        self.keyring_dir = keyring_dir or get_default_owner_keyring_dir()

    def ensure_keyring_dir(self) -> Path:
        self.keyring_dir.mkdir(parents=True, exist_ok=True)
        return self.keyring_dir

    @property
    def master_key_path(self) -> Path:
        return self.keyring_dir / "ed25519_master.key"

    @property
    def public_key_path(self) -> Path:
        return self.keyring_dir / "ed25519_public.pem"

    def generate_and_save_master_key(self) -> Tuple[bytes, bytes]:
        """
        Generates new Ed25519 keypair and saves master key locally protected with DPAPI.
        Returns: (private_key_bytes, public_key_bytes).
        """
        priv_bytes, pub_bytes = ed25519_generate_keypair()
        self.ensure_keyring_dir()

        # Encrypt private key with Windows DPAPI
        encrypted_priv = encrypt_dpapi(priv_bytes)
        with open(self.master_key_path, "wb") as f:
            f.write(encrypted_priv)

        # Write raw public key
        with open(self.public_key_path, "wb") as f:
            f.write(pub_bytes)

        logger.info(f"Generated new master keypair. Public FP: {compute_sha256_fingerprint(pub_bytes)}")
        return priv_bytes, pub_bytes

    def load_master_private_key(self) -> bytes:
        """
        Loads and decrypts master private key from local DPAPI storage.
        """
        if not self.master_key_path.is_file():
            raise KeyVaultError(f"Master key file not found: {self.master_key_path}")

        try:
            with open(self.master_key_path, "rb") as f:
                encrypted_blob = f.read()
            return decrypt_dpapi(encrypted_blob)
        except Exception as e:
            raise KeyVaultError(f"Failed to decrypt master private key via DPAPI: {e}") from e

    def load_public_key(self) -> bytes:
        """Loads raw 32-byte public key."""
        if not self.public_key_path.is_file():
            raise KeyVaultError(f"Public key file not found: {self.public_key_path}")
        with open(self.public_key_path, "rb") as f:
            return f.read()

    def export_vault(
        self,
        private_key_bytes: bytes,
        public_key_bytes: bytes,
        password: str,
        output_vault_path: Path,
        iterations: int = DEFAULT_PBKDF2_ITERATIONS
    ) -> Path:
        """
        Exports private key into portable AES-256-GCM encrypted .vault file.
        Format: [MAGIC (4B) | SALT (32B) | IV (12B) | GCM_TAG (16B) | CIPHERTEXT]
        """
        if len(private_key_bytes) != 32:
            raise KeyVaultError("Private key must be 32 bytes.")
        if len(public_key_bytes) != 32:
            raise KeyVaultError("Public key must be 32 bytes.")

        salt = secrets.token_bytes(SALT_SIZE)
        iv = secrets.token_bytes(IV_SIZE)

        # Derive 32-byte key
        key = derive_key_pbkdf2(password, salt, iterations=iterations, dklen=32)

        # Payload to encrypt: version (1B) + priv (32B) + pub (32B)
        payload = b"\x01" + private_key_bytes + public_key_bytes

        # Encrypt with AES-256-GCM
        ciphertext, tag = aes_gcm_encrypt(key, iv, payload, aad=VAULT_MAGIC)

        # Pack header and ciphertext
        container = VAULT_MAGIC + salt + iv + tag + ciphertext

        output_path = Path(output_vault_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(container)

        logger.info(f"Exported key vault to {output_path} ({len(container)} bytes)")
        return output_path

    def import_vault(
        self,
        vault_path: Path,
        password: str,
        iterations: int = DEFAULT_PBKDF2_ITERATIONS
    ) -> Tuple[bytes, bytes]:
        """
        Decrypts .vault container using password and returns (private_key_bytes, public_key_bytes).
        """
        path = Path(vault_path)
        if not path.is_file():
            raise KeyVaultError(f"Vault file not found: {path}")

        with open(path, "rb") as f:
            data = f.read()

        if len(data) < HEADER_SIZE:
            raise KeyVaultError(f"Vault file too small: {len(data)} bytes (expected >= {HEADER_SIZE})")

        magic = data[:4]
        if magic != VAULT_MAGIC:
            raise KeyVaultError(f"Invalid vault magic header: {magic} (expected {VAULT_MAGIC})")

        salt = data[4:4 + SALT_SIZE]
        iv = data[4 + SALT_SIZE:4 + SALT_SIZE + IV_SIZE]
        tag = data[4 + SALT_SIZE + IV_SIZE:HEADER_SIZE]
        ciphertext = data[HEADER_SIZE:]

        # Derive key
        key = derive_key_pbkdf2(password, salt, iterations=iterations, dklen=32)

        # Decrypt AES-256-GCM
        try:
            plaintext = aes_gcm_decrypt(key, iv, ciphertext, tag, aad=VAULT_MAGIC)
        except Exception as e:
            raise KeyVaultError("Vault decryption failed: invalid password or corrupted data.") from e

        if len(plaintext) != 65 or plaintext[0] != 1:
            raise KeyVaultError("Corrupted vault payload format.")

        priv_bytes = plaintext[1:33]
        pub_bytes = plaintext[33:65]
        return priv_bytes, pub_bytes

    # ==========================================================================
    # Integration Challenge Token Operations (Section 6.4)
    # ==========================================================================

    @staticmethod
    def create_challenge_payload(
        test_id: str,
        challenge_hex: str,
        public_key_fp: str,
        vault_fp: str,
        recovery_success: bool = True
    ) -> Dict[str, Any]:
        """
        Creates canonical integration test token payload per Section 6.4.
        """
        return {
            "version": "1.0",
            "test_id": test_id,
            "challenge": challenge_hex,
            "public_key_fp": public_key_fp,
            "vault_fp": vault_fp,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "recovery_success": recovery_success
        }

    @staticmethod
    def sign_challenge_token(
        payload: Dict[str, Any],
        private_key_bytes: bytes
    ) -> bytes:
        """
        Serializes payload to canonical JSON and signs with Ed25519 private key.
        Returns 64-byte raw signature.
        """
        canonical_bytes = canonical_json_bytes(payload)
        return ed25519_sign(private_key_bytes, canonical_bytes)

    @staticmethod
    def verify_challenge_token(
        payload: Dict[str, Any],
        signature_bytes: bytes,
        public_key_bytes: bytes
    ) -> bool:
        """
        Verifies Ed25519 signature over canonical challenge token.
        """
        canonical_bytes = canonical_json_bytes(payload)
        return ed25519_verify(public_key_bytes, canonical_bytes, signature_bytes)

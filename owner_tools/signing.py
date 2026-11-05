"""
Signing and Verification Engine for Owner Tools.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 5.1, 6.1, and 6.4.
Provides Ed25519 digital signature creation and validation for manifests and tokens.
"""

import json
import hashlib
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

from core.logger import get_logger
from core.exceptions import InvalidSignatureError
from owner_tools.crypto_backend import (
    ed25519_sign,
    ed25519_verify,
)
from owner_tools.key_vault import canonical_json_bytes, compute_sha256_fingerprint

logger = get_logger("signing")


class ManifestSigner:
    """
    Manages signing of release-manifest.json and verification with Ed25519 public key.
    """

    @staticmethod
    def compute_file_sha256(file_path: Path) -> str:
        """Computes SHA-256 hex digest of file."""
        p = Path(file_path)
        if not p.is_file():
            raise FileNotFoundError(f"File not found: {p}")
        h = hashlib.sha256()
        with open(p, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    @classmethod
    def sign_manifest_data(
        cls,
        manifest_dict: Dict[str, Any],
        private_key_bytes: bytes
    ) -> Tuple[bytes, bytes]:
        """
        Signs manifest dictionary using canonical JSON serialization and Ed25519 private key.
        Returns: (canonical_manifest_bytes, signature_bytes).
        """
        if len(private_key_bytes) != 32:
            raise ValueError("Private key must be 32 bytes.")

        canonical_bytes = canonical_json_bytes(manifest_dict)
        signature = ed25519_sign(private_key_bytes, canonical_bytes)
        return canonical_bytes, signature

    @classmethod
    def verify_manifest_signature(
        cls,
        manifest_bytes: bytes,
        signature_bytes: bytes,
        public_key_bytes: bytes
    ) -> bool:
        """
        Verifies Ed25519 signature over manifest bytes using public key.
        """
        if len(public_key_bytes) != 32:
            raise ValueError("Public key must be 32 bytes.")
        if len(signature_bytes) != 64:
            return False

        # Attempt JSON validation
        try:
            parsed = json.loads(manifest_bytes.decode("utf-8"))
            canonical_bytes = canonical_json_bytes(parsed)
        except Exception:
            canonical_bytes = manifest_bytes

        valid = ed25519_verify(public_key_bytes, canonical_bytes, signature_bytes)
        if not valid:
            logger.warning("Manifest signature verification failed.")
        return valid

    @classmethod
    def write_signed_manifest(
        cls,
        manifest_dict: Dict[str, Any],
        private_key_bytes: bytes,
        output_manifest_path: Path,
        output_sig_path: Path
    ) -> None:
        """
        Signs and writes release-manifest.json and release-manifest.sig.
        """
        canonical_bytes, sig = cls.sign_manifest_data(manifest_dict, private_key_bytes)
        output_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_manifest_path, "wb") as f:
            f.write(canonical_bytes)
        with open(output_sig_path, "wb") as f:
            f.write(sig)
        logger.info(f"Signed manifest written to {output_manifest_path} and {output_sig_path}")

"""
Release Manifest and Integrity Manager for Owner Tools.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 4.1, 5.1, and 12.2.
Builds canonical release-manifest.json, generates file hashes, and signs with Ed25519.
"""

import os
import json
import hashlib
import datetime
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

from core.logger import get_logger
from owner_tools.signing import ManifestSigner
from owner_tools.config_validator import ConfigValidator

logger = get_logger("release_manager")


class ReleaseManager:
    """
    Manages release packaging, file hashing, manifest compilation, and digital signing.
    """

    def __init__(self, stores_base_dir: Optional[Path] = None):
        self.stores_base_dir = stores_base_dir or Path("configs") / "stores"

    def build_store_manifest(
        self,
        store_id: str,
        custom_store_dir: Optional[Path] = None
    ) -> Dict[str, Any]:
        """
        Scans all files in store profile and generates manifest dictionary with SHA-256 hashes.
        """
        store_dir = custom_store_dir or (self.stores_base_dir / store_id)
        if not store_dir.is_dir():
            raise FileNotFoundError(f"Store directory not found: {store_dir}")

        # Validate configuration first
        ConfigValidator.validate_store_profile(store_dir)

        files_map: Dict[str, Dict[str, Any]] = {}

        # Recursively collect files excluding existing signature and manifest
        for root, _, filenames in sorted(os.walk(store_dir)):
            for fn in sorted(filenames):
                if fn in ("release-manifest.json", "release-manifest.sig"):
                    continue
                full_path = Path(root) / fn
                rel_path = full_path.relative_to(store_dir).as_posix()
                file_hash = ManifestSigner.compute_file_sha256(full_path)
                file_size = full_path.stat().st_size

                files_map[rel_path] = {
                    "sha256": file_hash,
                    "size_bytes": file_size
                }

        manifest = {
            "manifest_version": "1.0",
            "store_id": store_id,
            "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "files": files_map
        }

        logger.info(f"Built manifest for '{store_id}' with {len(files_map)} files.")
        return manifest

    def sign_and_save_store_release(
        self,
        store_id: str,
        private_key_bytes: bytes,
        custom_store_dir: Optional[Path] = None
    ) -> Tuple[Path, Path]:
        """
        Generates manifest, signs with Ed25519 private key, and writes:
        - release-manifest.json
        - release-manifest.sig
        """
        store_dir = custom_store_dir or (self.stores_base_dir / store_id)
        manifest_data = self.build_store_manifest(store_id, custom_store_dir=store_dir)

        manifest_path = store_dir / "release-manifest.json"
        sig_path = store_dir / "release-manifest.sig"

        ManifestSigner.write_signed_manifest(
            manifest_dict=manifest_data,
            private_key_bytes=private_key_bytes,
            output_manifest_path=manifest_path,
            output_sig_path=sig_path
        )
        return manifest_path, sig_path

    def verify_store_release(
        self,
        store_id: str,
        public_key_bytes: bytes,
        custom_store_dir: Optional[Path] = None
    ) -> bool:
        """
        Verifies release-manifest.sig using public key and validates all local file hashes.
        """
        store_dir = custom_store_dir or (self.stores_base_dir / store_id)
        manifest_path = store_dir / "release-manifest.json"
        sig_path = store_dir / "release-manifest.sig"

        if not manifest_path.is_file() or not sig_path.is_file():
            logger.warning(f"Store '{store_id}' is missing signed release manifest or signature.")
            return False

        with open(manifest_path, "rb") as f:
            manifest_bytes = f.read()
        with open(sig_path, "rb") as f:
            sig_bytes = f.read()

        # 1. Verify cryptographic signature
        if not ManifestSigner.verify_manifest_signature(manifest_bytes, sig_bytes, public_key_bytes):
            logger.error(f"Cryptographic signature check failed for store '{store_id}'!")
            return False

        # 2. Verify all file hashes
        data = json.loads(manifest_bytes.decode("utf-8"))
        files_map = data.get("files", {})

        for rel_path, file_meta in files_map.items():
            actual_file = store_dir / rel_path
            if not actual_file.is_file():
                logger.error(f"Manifest file missing from store directory: {rel_path}")
                return False
            actual_hash = ManifestSigner.compute_file_sha256(actual_file)
            if actual_hash != file_meta.get("sha256"):
                logger.error(f"Hash mismatch on {rel_path}: expected {file_meta.get('sha256')}, got {actual_hash}")
                return False

        logger.info(f"Store release '{store_id}' verified successfully.")
        return True

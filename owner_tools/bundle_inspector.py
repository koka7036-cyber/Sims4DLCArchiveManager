"""
Bundle Inspector and Release Security Auditor for Owner Tools.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 4.1, 5.1, 5.2, 6.1, 10, and 12.2.
Performs deep static and structural audit of PyInstaller bundles and release packages
to guarantee zero leakage of owner tools, credentials, and cross-store metadata.
"""

import os
import re
import zipfile
import hashlib
from pathlib import Path
from typing import Dict, Any, List, Set, Optional, Tuple

from core.logger import get_logger
from core.exceptions import SecurityError, WhiteLabelLeakError, ManifestVerificationError
from owner_tools.signing import ManifestSigner

logger = get_logger("bundle_inspector")

# Forbidden module names and packages that must never be bundled into the customer client
FORBIDDEN_MODULES: Set[str] = {
    "owner_tools",
    "owner_tools.key_vault",
    "owner_tools.token_manager",
    "owner_tools.build_manager",
    "owner_tools.release_manager",
    "owner_tools.signing",
    "owner_tools.config_validator",
    "owner_tools.builder_cli",
    "owner_tools.owner_gui",
    "owner_tools.bundle_inspector",
    "owner_tools.packaging",
}

# Forbidden file name patterns that must never exist in customer distribution
FORBIDDEN_FILE_PATTERNS: List[re.Pattern] = [
    re.compile(r"\.vault$", re.IGNORECASE),
    re.compile(r"\.key$", re.IGNORECASE),
    re.compile(r"ed25519_master.*", re.IGNORECASE),
    re.compile(r"^challenge_registry\.db.*", re.IGNORECASE),
    re.compile(r"^oauth\.dat$", re.IGNORECASE),
    re.compile(r"^\.env.*", re.IGNORECASE),
    re.compile(r".*\.db$", re.IGNORECASE),
    re.compile(r".*\.db-wal$", re.IGNORECASE),
    re.compile(r".*\.db-shm$", re.IGNORECASE),
    re.compile(r".*\.part$", re.IGNORECASE),
    re.compile(r"^uac_resume_.*\.dat$", re.IGNORECASE),
]

# Prohibited private key / credential substrings inside readable bundle files
FORBIDDEN_CONTENT_KEYWORDS: List[bytes] = [
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"OwnerTokenManager",
    b"execute_release_guard",
    b"challenge_registry.db",
]


class BundleAuditReport:
    """Detailed audit report containing inspected items and any detected security violations."""
    def __init__(self, target_path: Path):
        self.target_path = target_path
        self.is_valid: bool = True
        self.scanned_files_count: int = 0
        self.violations: List[str] = []
        self.detected_modules: List[str] = []
        self.detected_store_ids: Set[str] = set()

    def add_violation(self, message: str) -> None:
        self.violations.append(message)
        self.is_valid = False
        logger.error(f"Bundle Audit Violation: {message}")


class BundleInspector:
    """
    Audits PyInstaller spec files, output bundles, and customer release directories.
    """

    @staticmethod
    def audit_pyinstaller_spec(spec_file_path: Path) -> BundleAuditReport:
        """
        Audits a PyInstaller .spec file to ensure:
        1. 'owner_tools' is explicitly in excludes list.
        2. Windowed mode is enforced (console=False).
        3. UAC execution level is set to asInvoker.
        4. No secret file patterns are added to datas/binaries.
        """
        path = Path(spec_file_path)
        report = BundleAuditReport(path)
        if not path.is_file():
            report.add_violation(f"Spec file not found: {path}")
            return report

        content = path.read_text(encoding="utf-8", errors="replace")

        # 1. Check excludes contains owner_tools
        if "owner_tools" not in content or "excludes=" not in content:
            report.add_violation("Spec file does not explicitly exclude 'owner_tools' in Analysis.excludes.")

        # 2. Check console=False
        if not re.search(r"console\s*=\s*False", content):
            report.add_violation("Spec file must specify console=False for customer GUI application.")

        # 3. Check asInvoker manifest
        if "asInvoker" not in content:
            report.add_violation("Spec file must specify requestedExecutionLevel level='asInvoker' in manifest.")

        # 4. Check no secret patterns in bundled data files
        secret_data_patterns = [
            r"['\"][^'\"]*\.vault['\"]",
            r"['\"][^'\"]*\.key['\"]",
            r"['\"][^'\"]*challenge_registry\.db['\"]",
            r"['\"][^'\"]*\.env(\.[^'\"]*)?['\"]",
        ]
        for pattern in secret_data_patterns:
            if re.search(pattern, content):
                report.add_violation(f"Spec file contains reference to prohibited file pattern: {pattern}")

        return report

    @classmethod
    def audit_bundle_directory(
        cls,
        bundle_dir: Path,
        expected_store_id: Optional[str] = None
    ) -> BundleAuditReport:
        """
        Recursively scans a distribution directory (one-folder build or release bundle).
        Verifies absence of forbidden files, forbidden modules, and cross-store data.
        """
        root = Path(bundle_dir)
        report = BundleAuditReport(root)
        if not root.is_dir():
            report.add_violation(f"Bundle directory does not exist: {root}")
            return report

        for current_root, dirs, files in os.walk(root):
            rel_root = Path(current_root).relative_to(root).as_posix()
            
            # Check for forbidden directory names
            for d in list(dirs):
                if d == "owner_tools":
                    report.add_violation(f"Forbidden directory found in bundle: {rel_root}/{d}")
                if expected_store_id:
                    # Check for cross-store directory contamination
                    if d.startswith("store_") and d != expected_store_id:
                        report.add_violation(
                            f"Cross-store directory detected: found '{d}' in bundle for '{expected_store_id}'"
                        )

            for f in files:
                report.scanned_files_count += 1
                full_path = Path(current_root) / f
                rel_path = full_path.relative_to(root).as_posix()

                # Check filename against forbidden patterns
                for pat in FORBIDDEN_FILE_PATTERNS:
                    if pat.search(f):
                        report.add_violation(f"Forbidden file pattern detected in bundle: {rel_path}")

                # Check for cross-store file contamination
                if expected_store_id:
                    if f.startswith("store_") and not f.startswith(expected_store_id):
                        report.add_violation(
                            f"Cross-store file detected: '{rel_path}' belongs to different store than '{expected_store_id}'"
                        )

                # Inspect ZIP archives (such as base_library.zip or custom pyz)
                if f.endswith((".zip", ".pyz", ".pkg")):
                    cls._audit_archive(full_path, report)

                # Scan readable text/python/binary for private key or secret material
                if full_path.stat().st_size < 10 * 1024 * 1024:  # under 10MB
                    try:
                        data = full_path.read_bytes()
                        for kw in FORBIDDEN_CONTENT_KEYWORDS:
                            if kw in data:
                                report.add_violation(
                                    f"Prohibited secret keyword '{kw.decode('latin1', errors='replace')}' found in: {rel_path}"
                                )
                    except Exception:
                        pass

        return report

    @classmethod
    def _audit_archive(cls, archive_path: Path, report: BundleAuditReport) -> None:
        """Inspects internal members of an embedded archive for forbidden owner modules."""
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                for name in zf.namelist():
                    norm_name = name.replace("\\", "/")
                    if "owner_tools" in norm_name:
                        report.add_violation(
                            f"Archive '{archive_path.name}' contains forbidden module: {norm_name}"
                        )
                    for pat in FORBIDDEN_FILE_PATTERNS:
                        if pat.search(os.path.basename(norm_name)):
                            report.add_violation(
                                f"Archive '{archive_path.name}' contains forbidden file: {norm_name}"
                            )
        except Exception:
            # Not a standard zip archive, skip
            pass

    @classmethod
    def verify_release_integrity(
        cls,
        release_dir: Path,
        public_key_bytes: bytes,
        expected_store_id: str
    ) -> BundleAuditReport:
        """
        Performs complete release integrity verification:
        1. Audits release directory for forbidden files and cross-store leaks.
        2. Verifies Ed25519 signature of release-manifest.json.
        3. Validates SHA-256 hash of every release file against manifest.
        4. Validates SHA256.txt if present.
        """
        rel_dir = Path(release_dir)
        report = cls.audit_bundle_directory(rel_dir, expected_store_id=expected_store_id)
        if not report.is_valid:
            raise SecurityError(f"Release directory failed security audit: {report.violations}")

        manifest_file = rel_dir / "release-manifest.json"
        sig_file = rel_dir / "release-manifest.sig"

        if not manifest_file.is_file():
            raise ManifestVerificationError(f"Missing release manifest: {manifest_file}")
        if not sig_file.is_file():
            raise ManifestVerificationError(f"Missing release signature: {sig_file}")

        # 1. Verify Ed25519 signature
        valid_sig = ManifestSigner.verify_manifest_signature(
            manifest_bytes=manifest_file.read_bytes(),
            signature_bytes=sig_file.read_bytes(),
            public_key_bytes=public_key_bytes
        )
        if not valid_sig:
            raise ManifestVerificationError("Release manifest Ed25519 signature verification FAILED.")

        # 2. Check manifest content
        try:
            import json
            manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        except Exception as e:
            raise ManifestVerificationError(f"Failed to parse release manifest JSON: {e}") from e
        manifest_store_id = manifest_data.get("store_id")
        if manifest_store_id != expected_store_id:
            raise WhiteLabelLeakError(
                f"Release manifest store_id '{manifest_store_id}' does not match expected '{expected_store_id}'."
            )

        # 3. Check every file hash listed in manifest
        files_map = manifest_data.get("files", {})
        for rel_path, file_meta in files_map.items():
            target_file = rel_dir / rel_path
            if not target_file.is_file():
                raise ManifestVerificationError(f"File listed in manifest is missing from release: {rel_path}")

            expected_sha = file_meta.get("sha256")
            actual_sha = ManifestSigner.compute_file_sha256(target_file)
            if actual_sha.lower() != expected_sha.lower():
                raise ManifestVerificationError(
                    f"Hash mismatch for '{rel_path}': expected {expected_sha}, got {actual_sha}."
                )

        # 4. Check SHA256.txt if present
        sha256_txt = rel_dir / "SHA256.txt"
        if sha256_txt.is_file():
            cls._verify_sha256_checksums_file(sha256_txt, rel_dir)

        logger.info(f"Release integrity for '{expected_store_id}' successfully verified.")
        return report

    @staticmethod
    def _verify_sha256_checksums_file(sha256_txt: Path, rel_dir: Path) -> None:
        """Verifies files listed in standard SHA256.txt format: '<hash>  <filename>'."""
        with open(sha256_txt, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(None, 1)
                if len(parts) != 2:
                    continue
                expected_hash, fname = parts
                fname = fname.lstrip("*").strip()
                target_file = rel_dir / fname
                if not target_file.is_file():
                    raise ManifestVerificationError(f"File in SHA256.txt not found: {fname}")
                actual_hash = ManifestSigner.compute_file_sha256(target_file)
                if actual_hash.lower() != expected_hash.lower():
                    raise ManifestVerificationError(
                        f"Checksum mismatch in SHA256.txt for '{fname}': expected {expected_hash}, got {actual_hash}"
                    )

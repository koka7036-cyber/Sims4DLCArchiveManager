"""
Build Manager and Release Guard Engine for Owner Tools.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 6.3, 6.4, and 6.5.
Implements the Release Guard hardware fuse with atomic SQLite challenge verification.
"""

import os
import sys
import json
import sqlite3
import datetime
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from core.logger import get_logger
from core.exceptions import ChallengeError, SecurityError
from owner_tools.key_vault import (
    KeyVaultManager,
    canonical_json_bytes,
    parse_strict_json,
    compute_sha256_fingerprint,
)
from owner_tools.signing import ManifestSigner

logger = get_logger("build_manager")


class ReleaseGuardError(SecurityError):
    """Raised when Release Guard blocks the build pipeline."""
    pass


class BuildManager:
    """
    Coordinates release builds, executes automated testing, verifies integration test tokens,
    and manages the single-use challenge fuse in challenge_registry.db.
    """

    def __init__(self, owner_data_dir: Optional[Path] = None):
        self.owner_data_dir = owner_data_dir or (Path.home() / ".s4_owner_build")
        self.registry_db_path = self.owner_data_dir / "challenge_registry.db"
        self._init_registry_db()

    def _init_registry_db(self) -> None:
        """Initializes SQLite schema for challenges registry with WAL mode."""
        self.owner_data_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.registry_db_path))
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode = WAL;")
            cursor.execute("PRAGMA synchronous = FULL;")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS challenges (
                    challenge TEXT PRIMARY KEY,
                    test_id TEXT NOT NULL,
                    expected_public_key_fp TEXT NOT NULL,
                    expected_vault_fp TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('ISSUED', 'CONSUMED')),
                    created_at TEXT NOT NULL,
                    consumed_at TEXT
                );
            """)
            conn.commit()
        finally:
            conn.close()

    def issue_challenge(
        self,
        test_id: str,
        challenge_hex: str,
        expected_public_key_fp: str,
        expected_vault_fp: str
    ) -> None:
        """
        Registers a new single-use build challenge in ISSUED status.
        """
        conn = sqlite3.connect(str(self.registry_db_path))
        try:
            cursor = conn.cursor()
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            cursor.execute("""
                INSERT INTO challenges (
                    challenge, test_id, expected_public_key_fp, expected_vault_fp, status, created_at
                ) VALUES (?, ?, ?, ?, 'ISSUED', ?);
            """, (challenge_hex, test_id, expected_public_key_fp, expected_vault_fp, now_iso))
            conn.commit()
            logger.info(f"Issued challenge '{challenge_hex[:8]}...' for test '{test_id}'")
        finally:
            conn.close()

    def get_challenge_status(self, challenge_hex: str) -> Optional[str]:
        """Returns 'ISSUED', 'CONSUMED', or None if challenge does not exist."""
        conn = sqlite3.connect(str(self.registry_db_path))
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM challenges WHERE challenge = ?", (challenge_hex,))
            row = cursor.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def execute_release_guard(
        self,
        token_payload: Dict[str, Any],
        signature_bytes: bytes,
        public_key_bytes: bytes,
        vault_file_path: Path,
        skip_automated_subprocess: bool = False
    ) -> None:
        """
        Executes complete transactional Release Guard protocol per Section 6.5:
        1. Runs automated key vault test (subprocess).
        2. Verifies Ed25519 signature of integration test token.
        3. Validates public_key_fp and vault_fp match actual local files.
        4. Atomically marks challenge as CONSUMED (rowcount == 1).
        """
        # Step 1: Automated test run
        if not skip_automated_subprocess:
            test_file = Path("tests") / "test_key_vault_automated.py"
            if test_file.is_file():
                cmd = [sys.executable, "-m", "unittest", str(test_file)]
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode != 0:
                    raise ReleaseGuardError(
                        f"Release Guard rejected: Automated key vault test failed with code {res.returncode}.\n{res.stderr}"
                    )

        # Step 2: Signature verification
        valid_sig = KeyVaultManager.verify_challenge_token(
            payload=token_payload,
            signature_bytes=signature_bytes,
            public_key_bytes=public_key_bytes
        )
        if not valid_sig:
            raise ReleaseGuardError("Release Guard rejected: Invalid Ed25519 signature on integration test token.")

        if not token_payload.get("recovery_success", False):
            raise ReleaseGuardError("Release Guard rejected: Token reports recovery_success is False.")

        # Step 3: Fingerprint matching
        actual_pub_fp = compute_sha256_fingerprint(public_key_bytes)
        if token_payload.get("public_key_fp") != actual_pub_fp:
            raise ReleaseGuardError(
                f"Release Guard rejected: public_key_fp mismatch. Expected {token_payload.get('public_key_fp')}, got {actual_pub_fp}."
            )

        if not vault_file_path.is_file():
            raise ReleaseGuardError(f"Release Guard rejected: Vault file not found: {vault_file_path}")

        actual_vault_fp = compute_sha256_fingerprint(vault_file_path.read_bytes())
        if token_payload.get("vault_fp") != actual_vault_fp:
            raise ReleaseGuardError(
                f"Release Guard rejected: vault_fp mismatch. Expected {token_payload.get('vault_fp')}, got {actual_vault_fp}."
            )

        challenge_hex = token_payload.get("challenge")
        test_id = token_payload.get("test_id")

        # Step 4 & 5: Atomic single-use consumption in SQLite
        conn = sqlite3.connect(str(self.registry_db_path), isolation_level="EXCLUSIVE")
        try:
            cursor = conn.cursor()
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            cursor.execute("""
                UPDATE challenges
                SET status = 'CONSUMED', consumed_at = ?
                WHERE challenge = ?
                  AND test_id = ?
                  AND expected_public_key_fp = ?
                  AND expected_vault_fp = ?
                  AND status = 'ISSUED';
            """, (now_iso, challenge_hex, test_id, actual_pub_fp, actual_vault_fp))

            if cursor.rowcount != 1:
                conn.rollback()
                raise ReleaseGuardError(
                    f"Release Guard rejected: Challenge '{challenge_hex}' is invalid, already consumed, or parameter mismatch!"
                )

            conn.commit()
            logger.info(f"Release Guard PASSED: Challenge '{challenge_hex[:8]}' successfully consumed.")
        except Exception as e:
            conn.rollback()
            if isinstance(e, ReleaseGuardError):
                raise
            raise ReleaseGuardError(f"Database error during challenge consumption: {e}") from e
        finally:
            conn.close()

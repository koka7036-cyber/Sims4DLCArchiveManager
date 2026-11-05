"""
Unit tests for Handle-based Installer, Dry Run, and Rollback integration (Stage 7).
Strictly conforms to PROJECT_SPEC.md v1.1.5 sections 5.3, 7.3, 7.6, 8.3, 8.5.
"""

import os
import sys
import zipfile
import shutil
import tempfile
import unittest
from pathlib import Path

from core.app_context import AppContext, set_current_context
from core.exceptions import (
    RecoveryConflictError,
    LockContextError,
    LockLostException,
    TransactionError,
)
from core.security import acquire_lock_file, generate_lease_token, LockContext
from core.installer import DLCInstaller, DryRunReport
from database.models import (
    TransactionStatus,
    FileActionType,
    FileLogStatus,
)
from database.database import StateDatabase


class TestInstallerCrashRecovery(unittest.TestCase):
    """Unit tests for DLC installer, Dry Run, two-phase move, and rollback."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_installer_")
        self.appdata_dir = os.path.join(self.temp_dir, "LocalAppData")
        self.game_root = os.path.join(self.temp_dir, "The Sims 4")
        self.staging_dir = os.path.join(self.temp_dir, "staging")
        self.locks_dir = os.path.join(self.temp_dir, "locks")
        self.state_db_path = os.path.join(self.appdata_dir, "store_test", "database", "state.db")

        for d in (self.appdata_dir, self.game_root, self.staging_dir, self.locks_dir):
            os.makedirs(d, exist_ok=True)

        self.ctx = AppContext(
            store_id="store_test_installer",
            base_appdata_dir=self.appdata_dir
        )
        self.ctx.ensure_directories()
        set_current_context(self.ctx)

        self.state_db = StateDatabase(self.state_db_path)
        self.installer = DLCInstaller(self.state_db, self.ctx)

    def tearDown(self):
        self.state_db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_test_dlc_archive(self, archive_path: str, files_dict: dict) -> None:
        """Helper to create a valid DLC zip archive."""
        os.makedirs(os.path.dirname(os.path.abspath(archive_path)), exist_ok=True)
        with zipfile.ZipFile(archive_path, "w") as zf:
            for arc_name, content in files_dict.items():
                zf.writestr(arc_name, content)

    def test_dry_run_zero_disk_mutations(self):
        """
        Verify that Dry Run accurately classifies CREATE vs OVERWRITE
        without modifying or creating any files in the game directory.
        """
        # Pre-create an existing file in game directory (OVERWRITE target)
        existing_target = os.path.join(self.game_root, "EP01", "ClientDeltaBuild0.package")
        os.makedirs(os.path.dirname(existing_target), exist_ok=True)
        with open(existing_target, "wb") as f:
            f.write(b"ORIGINAL_DELTA_V1")

        archive_file = os.path.join(self.temp_dir, "EP01.zip")
        self._create_test_dlc_archive(archive_file, {
            "EP01/ClientFullBuild0.package": b"FULL_BUILD_NEW_DATA",
            "EP01/ClientDeltaBuild0.package": b"DELTA_BUILD_UPDATED_DATA",
        })

        # Run Dry Run
        report: DryRunReport = self.installer.dry_run(
            dlc_code="EP01",
            archive_path=archive_file,
            game_root=self.game_root
        )

        self.assertTrue(report.is_safe_to_proceed)
        self.assertEqual(report.total_files, 2)
        self.assertEqual(report.creates_count, 1)
        self.assertEqual(report.overwrites_count, 1)
        self.assertEqual(len(report.conflicts), 0)

        # Confirm 0 disk mutations in game root (new file must NOT exist)
        new_target = os.path.join(self.game_root, "EP01", "ClientFullBuild0.package")
        self.assertFalse(os.path.exists(new_target), "Dry run must NOT create files in game root!")

    def test_install_create_success(self):
        """
        Verify successful installation in CREATE mode:
        staging -> game root with ReplaceIfExists=FALSE, FSM state COMMITTED.
        """
        tx_id = "tx_create_1"
        lease_token = generate_lease_token()
        self.state_db.create_transaction(tx_id, "store_test_installer", "EP01", lease_token, os.getpid())

        archive_file = os.path.join(self.temp_dir, "EP01_create.zip")
        self._create_test_dlc_archive(archive_file, {
            "EP01/ClientFullBuild0.package": b"NEW_PACKAGE_DATA_CREATE"
        })

        lock_file = os.path.join(self.locks_dir, f"tx_{tx_id}.lock")
        lock_ctx = acquire_lock_file(lock_file, tx_id, lease_token)

        try:
            tx = self.installer.install_dlc(
                tx_id=tx_id,
                dlc_code="EP01",
                archive_path=archive_file,
                game_root=self.game_root,
                lock_context=lock_ctx,
                staging_root=self.staging_dir
            )

            self.assertEqual(tx.status, TransactionStatus.COMMITTED.value)

            target_file = os.path.join(self.game_root, "EP01", "ClientFullBuild0.package")
            self.assertTrue(os.path.exists(target_file))
            with open(target_file, "rb") as f:
                self.assertEqual(f.read(), b"NEW_PACKAGE_DATA_CREATE")

            # Check DB file log status
            f_log = self.state_db.get_file_log(tx_id, "EP01/ClientFullBuild0.package")
            self.assertEqual(f_log.status, FileLogStatus.APPLIED.value)
            self.assertEqual(f_log.action_type, FileActionType.CREATE.value)
        finally:
            lock_ctx.close()

    def test_install_overwrite_two_phase_success(self):
        """
        Verify successful installation in OVERWRITE mode:
        evacuation of original -> tx_backups -> staging installed -> COMMITTED.
        """
        tx_id = "tx_overwrite_1"
        lease_token = generate_lease_token()
        self.state_db.create_transaction(tx_id, "store_test_installer", "EP01", lease_token, os.getpid())

        # Pre-create original file
        orig_file = os.path.join(self.game_root, "EP01", "ExistingFile.package")
        os.makedirs(os.path.dirname(orig_file), exist_ok=True)
        with open(orig_file, "wb") as f:
            f.write(b"ORIGINAL_DATA_V1")

        archive_file = os.path.join(self.temp_dir, "EP01_overwrite.zip")
        self._create_test_dlc_archive(archive_file, {
            "EP01/ExistingFile.package": b"NEW_OVERWRITTEN_DATA_V2"
        })

        lock_file = os.path.join(self.locks_dir, f"tx_{tx_id}.lock")
        lock_ctx = acquire_lock_file(lock_file, tx_id, lease_token)

        try:
            tx = self.installer.install_dlc(
                tx_id=tx_id,
                dlc_code="EP01",
                archive_path=archive_file,
                game_root=self.game_root,
                lock_context=lock_ctx,
                staging_root=self.staging_dir
            )

            self.assertEqual(tx.status, TransactionStatus.COMMITTED.value)

            with open(orig_file, "rb") as f:
                self.assertEqual(f.read(), b"NEW_OVERWRITTEN_DATA_V2")

            f_log = self.state_db.get_file_log(tx_id, "EP01/ExistingFile.package")
            self.assertEqual(f_log.status, FileLogStatus.APPLIED.value)
            self.assertEqual(f_log.action_type, FileActionType.OVERWRITE.value)
        finally:
            lock_ctx.close()

    def test_install_without_active_lock_context_raises(self):
        """
        Verify that attempting to install without an active LockContext raises LockContextError.
        """
        tx_id = "tx_nolock_1"
        lease_token = generate_lease_token()
        self.state_db.create_transaction(tx_id, "store_test_installer", "EP01", lease_token, os.getpid())

        archive_file = os.path.join(self.temp_dir, "EP01_nolock.zip")
        self._create_test_dlc_archive(archive_file, {"EP01/file.package": b"data"})

        lock_file = os.path.join(self.locks_dir, f"tx_{tx_id}.lock")
        lock_ctx = acquire_lock_file(lock_file, tx_id, lease_token)
        lock_ctx.close()  # Invalidate lock!

        with self.assertRaises((LockContextError, LockLostException)):
            self.installer.install_dlc(
                tx_id=tx_id,
                dlc_code="EP01",
                archive_path=archive_file,
                game_root=self.game_root,
                lock_context=lock_ctx,
                staging_root=self.staging_dir
            )


    def test_create_collision_triggers_conflict(self):
        """
        Verify that if target file unexpectedly exists during _apply_create,
        it fails with RecoveryConflictError and does not overwrite target.
        """
        tx_id = "tx_collision_create"
        lease_token = generate_lease_token()
        self.state_db.create_transaction(tx_id, "store_test_installer", "EP01", lease_token, os.getpid())

        # Pre-create colliding file
        target_file = os.path.join(self.game_root, "EP01", "Colliding.package")
        os.makedirs(os.path.dirname(target_file), exist_ok=True)
        with open(target_file, "wb") as f:
            f.write(b"ORIGINAL_FOREIGN_FILE")

        archive_file = os.path.join(self.temp_dir, "EP01_collision.zip")
        self._create_test_dlc_archive(archive_file, {
            "EP01/Colliding.package": b"NEW_DATA"
        })

        lock_file = os.path.join(self.locks_dir, f"tx_{tx_id}.lock")
        lock_ctx = acquire_lock_file(lock_file, tx_id, lease_token)

        try:
            # Manually invoke _apply_create with existing target to test conflict branch
            staging_file = os.path.join(self.staging_dir, "staged.package")
            with open(staging_file, "wb") as f:
                f.write(b"NEW_DATA")

            with lock_ctx.guard():
                with self.assertRaises(RecoveryConflictError):
                    self.installer._apply_create(
                        tx_id=tx_id,
                        rel_path="EP01/Colliding.package",
                        target_file=target_file,
                        staging_file=staging_file,
                        staging_file_id="12345",
                        target_sha256="abc",
                        lock_context=lock_ctx
                    )

            # Foreign file must remain untouched
            with open(target_file, "rb") as f:
                self.assertEqual(f.read(), b"ORIGINAL_FOREIGN_FILE")

            f_log = self.state_db.get_file_log(tx_id, "EP01/Colliding.package")
            self.assertEqual(f_log.status, FileLogStatus.CONFLICT.value)
        finally:
            lock_ctx.close()

    def test_triple_integrity_checks(self):
        """Test triple identity verification in core.integrity."""
        from core.integrity import assert_verified_object
        from core.exceptions import HashMismatchError
        import hashlib

        test_file = os.path.join(self.temp_dir, "verify_test.bin")
        content = b"DATA_FOR_VERIFICATION"
        with open(test_file, "wb") as f:
            f.write(content)
        correct_sha = hashlib.sha256(content).hexdigest()

        # Success check
        vol, fid, sha = assert_verified_object(test_file, expected_sha256=correct_sha)
        self.assertEqual(sha, correct_sha)

        # Hash mismatch check
        wrong_sha = "0000000000000000000000000000000000000000000000000000000000000000"
        with self.assertRaises(HashMismatchError):
            assert_verified_object(test_file, expected_sha256=wrong_sha)

    def test_hardlink_anomalies_protection(self):
        """Test hardlink anomaly detector in core.integrity."""
        from core.integrity import detect_hardlink_anomalies

        f1 = os.path.join(self.temp_dir, "hl1.bin")
        f2 = os.path.join(self.temp_dir, "hl2.bin")
        f3 = os.path.join(self.temp_dir, "hl3.bin")

        with open(f1, "wb") as f:
            f.write(b"content1")
        with open(f3, "wb") as f:
            f.write(b"content3")

        # Create physical hard link between f1 and f2
        os.link(f1, f2)

        # Should detect hardlink anomaly between target (f1) and backup (f2)
        has_anomaly = detect_hardlink_anomalies(target_path=f1, backup_path=f2, staging_path=f3)
        self.assertTrue(has_anomaly)

        # Without hardlink:
        has_no_anomaly = detect_hardlink_anomalies(target_path=f1, backup_path=f3, staging_path=None)
        self.assertFalse(has_no_anomaly)


if __name__ == "__main__":
    unittest.main()

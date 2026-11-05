"""
Tests for Backup and Rollback Manager (Stage 7).
Strictly conforms to PROJECT_SPEC.md v1.1.5 sections 5.2, 5.3, 7.3, 11.
"""

import os
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

from core.app_context import AppContext, set_current_context
from core.backup_manager import BackupManager, UserBackupMetadata
from core.exceptions import RecoveryConflictError, LockContextError
from core.security import acquire_lock_file, generate_lease_token


class TestBackupManager(unittest.TestCase):
    """Unit tests for transactional backups and user data backups."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_backup_mgr_")
        self.appdata_dir = os.path.join(self.temp_dir, "LocalAppData")
        self.game_root = os.path.join(self.temp_dir, "The Sims 4")
        self.user_dir = os.path.join(self.temp_dir, "User_The_Sims_4")
        self.locks_dir = os.path.join(self.temp_dir, "locks")
        self.tx_backups_dir = os.path.join(self.temp_dir, "tx_backups")

        for d in (self.appdata_dir, self.game_root, self.user_dir, self.locks_dir, self.tx_backups_dir):
            os.makedirs(d, exist_ok=True)

        self.ctx = AppContext(
            store_id="store_test_backup",
            base_appdata_dir=self.appdata_dir
        )
        self.ctx.ensure_directories()
        set_current_context(self.ctx)

        self.mgr = BackupManager(self.ctx)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_transactional_backup_create_and_restore(self):
        """
        Verify transactional backup creation and safe restoration with ReplaceIfExists=FALSE
        under LockContext.guard().
        """
        tx_id = "tx_bak_test_1"
        lease_token = generate_lease_token()
        lock_file = os.path.join(self.locks_dir, f"tx_{tx_id}.lock")
        lock_ctx = acquire_lock_file(lock_file, tx_id, lease_token)

        try:
            # Create original file
            orig_file = os.path.join(self.game_root, "original.package")
            with open(orig_file, "wb") as f:
                f.write(b"ORIGINAL_GAME_CONTENT_V1")

            # 1. Create tx backup
            backup_path, orig_sha, orig_fid, orig_vol = self.mgr.create_tx_backup(
                tx_id=tx_id,
                original_game_file=orig_file,
                tx_backup_dir=self.tx_backups_dir,
                lock_context=lock_ctx
            )

            self.assertTrue(os.path.exists(backup_path))
            self.assertFalse(os.path.exists(orig_file), "Original file should have been evacuated from game root")

            # 2. Restore backup back to target
            self.mgr.restore_tx_backup(
                backup_path=backup_path,
                target_path=orig_file,
                expected_sha256=orig_sha,
                expected_file_id=orig_fid,
                lock_context=lock_ctx
            )

            self.assertTrue(os.path.exists(orig_file))
            with open(orig_file, "rb") as f:
                self.assertEqual(f.read(), b"ORIGINAL_GAME_CONTENT_V1")
            self.assertFalse(os.path.exists(backup_path))
        finally:
            lock_ctx.close()

    def test_restore_tx_backup_collision_fails(self):
        """
        Verify that restoring a backup fails with RecoveryConflictError if the target path
        already exists (ReplaceIfExists = FALSE).
        """
        backup_file = os.path.join(self.tx_backups_dir, "backup.package")
        with open(backup_file, "wb") as f:
            f.write(b"BACKUP_CONTENT")

        target_file = os.path.join(self.game_root, "existing.package")
        with open(target_file, "wb") as f:
            f.write(b"COLLIDING_TARGET_FILE")

        import hashlib
        sha = hashlib.sha256(b"BACKUP_CONTENT").hexdigest()

        with self.assertRaises(RecoveryConflictError):
            self.mgr.restore_tx_backup(
                backup_path=backup_file,
                target_path=target_file,
                expected_sha256=sha
            )

    def test_user_backup_create_list_and_restore(self):
        """
        Verify user data backup creation (Saves, Tray, Mods), manifest generation,
        and restoration to user directory.
        """
        # Create mock user directories
        saves_dir = os.path.join(self.user_dir, "saves")
        tray_dir = os.path.join(self.user_dir, "Tray")
        mods_dir = os.path.join(self.user_dir, "Mods")
        os.makedirs(saves_dir, exist_ok=True)
        os.makedirs(tray_dir, exist_ok=True)
        os.makedirs(mods_dir, exist_ok=True)

        with open(os.path.join(saves_dir, "Slot_00000001.save"), "wb") as f:
            f.write(b"SAVE_DATA_1")
        with open(os.path.join(tray_dir, "TrayItem_001.trayitem"), "wb") as f:
            f.write(b"TRAY_DATA_1")
        with open(os.path.join(mods_dir, "CustomMod.package"), "wb") as f:
            f.write(b"MOD_DATA_1")

        # 1. Dry Run test
        dry_meta = self.mgr.create_user_backup(
            backup_type="ALL",
            custom_user_dir=self.user_dir,
            dry_run=True
        )
        self.assertEqual(dry_meta.file_count, 3)
        self.assertFalse(os.path.exists(dry_meta.archive_path), "Dry run must not create archive on disk")

        # 2. Real user backup
        meta = self.mgr.create_user_backup(
            backup_type="ALL",
            custom_user_dir=self.user_dir,
            dry_run=False
        )
        self.assertTrue(os.path.exists(meta.archive_path))
        self.assertEqual(meta.file_count, 3)

        # 3. List backups
        backups = self.mgr.list_user_backups()
        self.assertGreaterEqual(len(backups), 1)
        self.assertEqual(backups[0].backup_id, meta.backup_id)

        # 4. Restore to new directory
        restore_dir = os.path.join(self.temp_dir, "Restored_User_Sims_4")
        count = self.mgr.restore_user_backup(meta.archive_path, restore_dir)
        self.assertEqual(count, 3)
        self.assertTrue(os.path.exists(os.path.join(restore_dir, "saves", "Slot_00000001.save")))
        self.assertTrue(os.path.exists(os.path.join(restore_dir, "Tray", "TrayItem_001.trayitem")))
        self.assertTrue(os.path.exists(os.path.join(restore_dir, "Mods", "CustomMod.package")))


if __name__ == "__main__":
    unittest.main()

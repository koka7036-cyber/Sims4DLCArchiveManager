"""
Backup and Rollback Manager for Sims 4 DLC Archive & Install Manager.
Strictly conforms to PROJECT_SPEC.md v1.1.5 sections 5.2, 5.3, 7.3, 11.
Manages both:
1. Transactional file backups (tx_backups on the same NTFS volume as the game)
2. User data backups (Saves, Tray, Mods) under %LOCALAPPDATA%/{store_id}/user_backups/
"""

from __future__ import annotations

import os
import sys
import json
import uuid
import shutil
import zipfile
import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any, Tuple

from core.app_context import get_current_context, AppContext
from core.exceptions import (
    FilesystemError,
    IntegrityError,
    RecoveryConflictError,
    SharingViolationError,
    LockContextError,
)
from core.logger import get_logger
from core.security import LockContext
from core.integrity import assert_verified_object, verify_triple_identity
from core.nt_handle import move_file_replace_if_exists_false
from database.recovery import get_file_identity, compute_sha256

logger = get_logger("core.backup_manager")


@dataclass
class UserBackupMetadata:
    """Metadata for a user data backup archive."""
    backup_id: str
    store_id: str
    backup_type: str  # ALL, SAVES, TRAY, MODS
    archive_filename: str
    archive_path: str
    created_at: str
    size_bytes: int
    file_count: int
    source_dir: str


class BackupManager:
    """
    Manages transactional game backups and user data backups (Saves, Tray, Mods).
    """

    def __init__(self, app_context: Optional[AppContext] = None):
        self.ctx = app_context or get_current_context()

    # ==========================================================================
    # Transactional Game Backups (tx_backups on same volume)
    # ==========================================================================

    def create_tx_backup(
        self,
        tx_id: str,
        original_game_file: str,
        tx_backup_dir: str,
        lock_context: LockContext
    ) -> Tuple[str, str, int, str]:
        """
        Safely evacuates original game file to tx_backups directory with ReplaceIfExists=FALSE
        under lock_context.guard().
        Returns: (backup_file_path, original_sha256, original_file_id, original_volume_serial).
        """
        with lock_context.guard():
            orig_path = os.path.abspath(original_game_file)
            if not os.path.exists(orig_path):
                raise FileNotFoundError(f"Original file for backup does not exist: '{orig_path}'")

            # 1. Capture original identity and hash
            vol_serial, file_id = get_file_identity(orig_path)
            orig_sha = compute_sha256(orig_path)

            # 2. Compute destination backup path
            os.makedirs(tx_backup_dir, exist_ok=True)
            backup_filename = f"bak_{tx_id}_{file_id}_{os.path.basename(orig_path)}"
            backup_path = os.path.join(tx_backup_dir, backup_filename)

            # 3. Move original to tx_backups with ReplaceIfExists = FALSE
            move_file_replace_if_exists_false(orig_path, backup_path, lock_context)

            # 4. Verify backup identity and hash
            b_vol, b_fid, b_sha = assert_verified_object(
                backup_path,
                expected_volume_serial=vol_serial,
                expected_file_id=str(file_id),
                expected_sha256=orig_sha,
                lock_context=lock_context
            )

            logger.info(f"Transactional backup created: '{orig_path}' -> '{backup_path}' (sha={orig_sha[:8]}...)")
            return backup_path, orig_sha, str(file_id), vol_serial

    def restore_tx_backup(
        self,
        backup_path: str,
        target_path: str,
        expected_sha256: str,
        expected_file_id: Optional[str] = None,
        lock_context: Optional[LockContext] = None
    ) -> None:
        """
        Restores evacuated backup file to target game path with ReplaceIfExists = FALSE.
        Fails if target file already exists or backup is corrupted.
        """
        guard_ctx = lock_context.guard() if lock_context else None
        if guard_ctx:
            with guard_ctx:
                self._execute_restore(backup_path, target_path, expected_sha256, expected_file_id, lock_context)
        else:
            self._execute_restore(backup_path, target_path, expected_sha256, expected_file_id, None)

    def _execute_restore(
        self,
        backup_path: str,
        target_path: str,
        expected_sha256: str,
        expected_file_id: Optional[str],
        lock_context: Optional[LockContext]
    ) -> None:
        if not os.path.exists(backup_path):
            raise FileNotFoundError(f"Backup file not found for restore: '{backup_path}'")

        if os.path.exists(target_path):
            raise RecoveryConflictError(
                f"Target file already exists: '{target_path}'. Cannot restore without overwrite collision."
            )

        # Verify backup integrity before restore
        assert_verified_object(
            backup_path,
            expected_file_id=expected_file_id,
            expected_sha256=expected_sha256,
            lock_context=lock_context
        )

        # Relocate backup back to target
        move_file_replace_if_exists_false(backup_path, target_path, lock_context)
        logger.info(f"Restored transactional backup: '{backup_path}' -> '{target_path}'")

    # ==========================================================================
    # User Data Backups (Saves, Tray, Mods)
    # ==========================================================================

    @staticmethod
    def get_default_sims4_user_dir() -> str:
        """
        Returns default Electronic Arts/The Sims 4 folder in Documents.
        On Windows: %USERPROFILE%/Documents/Electronic Arts/The Sims 4
        """
        if sys.platform == "win32":
            user_profile = os.environ.get("USERPROFILE", str(Path.home()))
            return os.path.join(user_profile, "Documents", "Electronic Arts", "The Sims 4")
        return os.path.join(str(Path.home()), "Documents", "Electronic Arts", "The Sims 4")

    def create_user_backup(
        self,
        backup_type: str = "ALL",  # ALL, SAVES, TRAY, MODS
        custom_user_dir: Optional[str] = None,
        dry_run: bool = False
    ) -> UserBackupMetadata:
        """
        Packages Saves, Tray, and/or Mods into a timestamped zip archive
        in %LOCALAPPDATA%/{store_id}/user_backups/.
        If dry_run=True, computes size and file count without creating archive.
        """
        source_dir = os.path.abspath(custom_user_dir or self.get_default_sims4_user_dir())
        if not os.path.exists(source_dir):
            raise FileNotFoundError(f"The Sims 4 user directory not found at: '{source_dir}'")

        subdirs_map = {
            "SAVES": ["saves"],
            "TRAY": ["Tray"],
            "MODS": ["Mods"],
            "ALL": ["saves", "Tray", "Mods"],
        }
        b_type = backup_type.upper()
        if b_type not in subdirs_map:
            raise ValueError(f"Invalid backup_type: '{backup_type}'. Allowed: ALL, SAVES, TRAY, MODS.")

        selected_subdirs = subdirs_map[b_type]
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_id = f"ubak_{b_type.lower()}_{uuid.uuid4().hex[:8]}"
        archive_name = f"user_backup_{b_type.lower()}_{timestamp}.zip"
        archive_path = os.path.join(self.ctx.user_backups_dir, archive_name)

        # Collect files to pack
        files_to_pack = []
        total_bytes = 0
        for subdir_name in selected_subdirs:
            full_sub = os.path.join(source_dir, subdir_name)
            if os.path.exists(full_sub):
                for root, _, files in os.walk(full_sub):
                    for file in files:
                        fp = os.path.join(root, file)
                        rel = os.path.relpath(fp, source_dir)
                        sz = os.path.getsize(fp)
                        files_to_pack.append((fp, rel))
                        total_bytes += sz

        meta = UserBackupMetadata(
            backup_id=backup_id,
            store_id=self.ctx.store_id,
            backup_type=b_type,
            archive_filename=archive_name,
            archive_path=archive_path,
            created_at=datetime.datetime.now().isoformat(),
            size_bytes=total_bytes,
            file_count=len(files_to_pack),
            source_dir=source_dir
        )

        if dry_run:
            logger.info(f"Dry Run User Backup ({b_type}): {len(files_to_pack)} files, {total_bytes} bytes")
            return meta

        os.makedirs(self.ctx.user_backups_dir, exist_ok=True)

        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for src_file, arc_rel in files_to_pack:
                zf.write(src_file, arc_rel)
            # Write metadata manifest into archive
            zf.writestr("backup_manifest.json", json.dumps(asdict(meta), indent=2))

        logger.info(f"Created user backup '{archive_name}' with {len(files_to_pack)} files ({total_bytes} bytes)")
        return meta

    def list_user_backups(self) -> List[UserBackupMetadata]:
        """Lists existing user backup archives in the store's backup directory."""
        if not os.path.exists(self.ctx.user_backups_dir):
            return []

        backups = []
        for file in sorted(os.listdir(self.ctx.user_backups_dir), reverse=True):
            if file.endswith(".zip") and file.startswith("user_backup_"):
                fp = os.path.join(self.ctx.user_backups_dir, file)
                try:
                    with zipfile.ZipFile(fp, "r") as zf:
                        if "backup_manifest.json" in zf.namelist():
                            data = json.loads(zf.read("backup_manifest.json").decode("utf-8"))
                            backups.append(UserBackupMetadata(**data))
                        else:
                            st = os.stat(fp)
                            backups.append(UserBackupMetadata(
                                backup_id=file,
                                store_id=self.ctx.store_id,
                                backup_type="UNKNOWN",
                                archive_filename=file,
                                archive_path=fp,
                                created_at=datetime.datetime.fromtimestamp(st.st_mtime).isoformat(),
                                size_bytes=st.st_size,
                                file_count=len(zf.namelist()),
                                source_dir=""
                            ))
                except Exception as e:
                    logger.debug(f"Could not read backup '{file}': {e}")
                    continue
        return backups

    def restore_user_backup(
        self,
        archive_path: str,
        destination_dir: Optional[str] = None
    ) -> int:
        """
        Restores a user backup zip archive to the Sims 4 user folder.
        Returns count of restored files.
        """
        dest = os.path.abspath(destination_dir or self.get_default_sims4_user_dir())
        if not os.path.exists(archive_path):
            raise FileNotFoundError(f"User backup archive not found: '{archive_path}'")

        os.makedirs(dest, exist_ok=True)
        restored_count = 0

        with zipfile.ZipFile(archive_path, "r") as zf:
            for member in zf.namelist():
                if member == "backup_manifest.json" or member.endswith("/"):
                    continue
                # Sanitize member name
                clean_rel = os.path.normpath(member).replace("\\", "/")
                if clean_rel.startswith("../") or clean_rel.startswith("/"):
                    continue
                out_path = os.path.join(dest, clean_rel)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with zf.open(member) as src, open(out_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                restored_count += 1

        logger.info(f"Restored {restored_count} user files to '{dest}'")
        return restored_count

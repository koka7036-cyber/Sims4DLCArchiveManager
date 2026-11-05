"""
Handle-based Two-Phase DLC Installer and Dry Run Engine.
Strictly conforms to PROJECT_SPEC.md v1.1.5 sections 5.3, 7.1, 7.2, 7.3, 7.6, 8.1, 8.3, 8.4, 8.5, 8.6.

Implements:
1. Strict two-phase CREATE and OVERWRITE handle-based file relocation.
2. Complete absence of TOCTOU races: ReplaceIfExists = FALSE, handle ownership.
3. Continuous LockContext.guard() contract during Win32/NT calls.
4. Comprehensive Dry Run and Verify Only mode with 0 disk mutations.
5. Atomic rollback on error using transaction_file_log and tx_backups.
"""

from __future__ import annotations

import os
import sys
import shutil
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Any

from core.app_context import AppContext, get_current_context
from core.archive_manager import ArchiveManager
from core.archive_inspector import DLCArchiveInspector, InspectionReport
from core.exceptions import (
    FilesystemError,
    IntegrityError,
    RecoveryConflictError,
    SharingViolationError,
    TransactionError,
    LockContextError,
    LockLostException,
    UnsupportedFilesystemError,
    BlockedBinaryError,
    PathTraversalError,
)
from core.game_finder import verify_ntfs_filesystem
from core.logger import get_logger
from core.security import LockContext
from core.integrity import (
    assert_verified_object,
    verify_triple_identity,
    detect_hardlink_anomalies,
)
from core.nt_handle import (
    step_traverse_directories,
    move_file_replace_if_exists_false,
    SafeNTHandle,
)
from database.models import (
    TransactionStatus,
    LeaseStatus,
    FileActionType,
    FileLogStatus,
    InspectionResult,
    TransactionRecord,
)
from database.database import StateDatabase
from database.recovery import (
    CrashRecoveryEngine,
    get_file_identity,
    compute_sha256,
)

logger = get_logger("core.installer")


@dataclass
class DryRunReport:
    """Detailed audit report produced by Dry Run simulation without disk modification."""
    dlc_code: str
    archive_path: str
    total_files: int
    creates_count: int
    overwrites_count: int
    total_incoming_bytes: int
    total_backup_bytes_needed: int
    planned_creates: List[str] = field(default_factory=list)
    planned_overwrites: List[Tuple[str, int]] = field(default_factory=list)  # (rel_path, orig_size)
    conflicts: List[str] = field(default_factory=list)
    is_safe_to_proceed: bool = True


class DLCInstaller:
    """
    Orchestrates controlled, handle-based DLC installations with two-phase evacuation,
    ReplaceIfExists = FALSE, and atomic Rollback under LockContext protection.
    """

    def __init__(self, state_db: StateDatabase, app_context: Optional[AppContext] = None):
        self.state_db = state_db
        self.ctx = app_context or get_current_context()

    # ==========================================================================
    # Dry Run / Verify Only Mode (0 disk modifications)
    # ==========================================================================

    def dry_run(
        self,
        dlc_code: str,
        archive_path: str,
        game_root: str
    ) -> DryRunReport:
        """
        Performs full pre-installation audit without writing any bytes to the game directory:
        1. Tests archive integrity and structure via DLCArchiveInspector.
        2. Normalizes relative paths to game root.
        3. Identifies CREATE vs OVERWRITE for every entry.
        4. Calculates required backup storage size.
        5. Detects potential conflicts (sharing violations, permissions).
        """
        norm_game_root = os.path.abspath(game_root)
        if not os.path.isdir(norm_game_root):
            raise FileNotFoundError(f"The Sims 4 game directory not found: '{norm_game_root}'")

        # Verify archive integrity and extract safe relative paths
        inspector = DLCArchiveInspector()
        report = inspector.inspect_archive(archive_path, is_helper_archive=False, target_dir=norm_game_root)
        if not report.is_valid:
            raise TransactionError(f"Archive inspection failed: {report.error_message}")

        planned_creates: List[str] = []
        planned_overwrites: List[Tuple[str, int]] = []
        conflicts: List[str] = []
        total_incoming_bytes = 0
        total_backup_bytes = 0

        for entry in report.entries:
            if entry.is_dir:
                continue

            rel_path = report.safe_relative_paths.get(entry.path)
            if not rel_path:
                continue

            total_incoming_bytes += entry.uncompressed_size
            target_path = os.path.join(norm_game_root, rel_path)

            if os.path.exists(target_path):
                # Target exists -> OVERWRITE candidate
                try:
                    orig_size = os.path.getsize(target_path)
                    planned_overwrites.append((rel_path, orig_size))
                    total_backup_bytes += orig_size
                except OSError as e:
                    conflicts.append(f"Cannot read existing file '{rel_path}': {e}")
            else:
                # Target does not exist -> CREATE candidate
                planned_creates.append(rel_path)

        is_safe = len(conflicts) == 0

        logger.info(
            f"Dry Run for {dlc_code}: {len(planned_creates)} CREATE, {len(planned_overwrites)} OVERWRITE, "
            f"{total_backup_bytes} backup bytes, conflicts={len(conflicts)}"
        )

        return DryRunReport(
            dlc_code=dlc_code,
            archive_path=str(archive_path),
            total_files=len(planned_creates) + len(planned_overwrites),
            creates_count=len(planned_creates),
            overwrites_count=len(planned_overwrites),
            total_incoming_bytes=total_incoming_bytes,
            total_backup_bytes_needed=total_backup_bytes,
            planned_creates=planned_creates,
            planned_overwrites=planned_overwrites,
            conflicts=conflicts,
            is_safe_to_proceed=is_safe
        )

    # ==========================================================================
    # Controlled Installation
    # ==========================================================================

    def install_dlc(
        self,
        tx_id: str,
        dlc_code: str,
        archive_path: str,
        game_root: str,
        lock_context: LockContext,
        staging_root: Optional[str] = None
    ) -> TransactionRecord:
        """
        Executes controlled DLC installation following Section 7.3:
        1. Validates active LockContext.
        2. Validates game volume filesystem (NTFS requirement).
        3. Prepares staging and tx_backups on the SAME NTFS volume.
        4. Extracts files to staging, computing SHA-256 and FileId.
        5. Logs intentions into state.db before disk changes (synchronous=FULL).
        6. Applies files via two-phase move under lock_context.guard() with ReplaceIfExists=FALSE.
        7. On any error, triggers automatic rollback and raises exception.
        """
        if lock_context.tx_id != tx_id:
            raise LockContextError(f"LockContext tx_id mismatch: {lock_context.tx_id} != {tx_id}")

        norm_game_root = os.path.abspath(game_root)
        if not os.path.isdir(norm_game_root):
            raise FileNotFoundError(f"Game root not found: {norm_game_root}")

        # Section 5.3: Verify NTFS filesystem
        if not verify_ntfs_filesystem(norm_game_root):
            raise UnsupportedFilesystemError(f"Volume for '{norm_game_root}' is not NTFS. Installation blocked.")

        # Determine staging root on same volume per Section 5.3
        if not staging_root:
            staging_hash = hashlib.sha256(f"{norm_game_root}_{tx_id}".encode()).hexdigest()[:10]
            staging_root = os.path.join(
                os.path.dirname(norm_game_root),
                f".s4_staging_{self.ctx.store_id}_{staging_hash}"
            )

        staging_incoming = os.path.join(staging_root, "incoming")
        staging_backups = os.path.join(staging_root, "tx_backups")
        os.makedirs(staging_incoming, exist_ok=True)
        os.makedirs(staging_backups, exist_ok=True)

        # 1. Inspect archive
        inspector = DLCArchiveInspector()
        report = inspector.inspect_archive(archive_path, is_helper_archive=False, target_dir=norm_game_root)
        if not report.is_valid:
            raise TransactionError(f"Archive verification failed: {report.error_message}")

        # Transition transaction into PREPARED then INSTALLING
        tx = self.state_db.get_transaction(tx_id)
        if not tx:
            raise TransactionError(f"Transaction '{tx_id}' not found in state.db.")

        if tx.status == TransactionStatus.CREATED.value:
            self.state_db.update_transaction_status(tx_id, TransactionStatus.PREPARED, TransactionStatus.CREATED)

        # 2. Extract approved files to staging and compute hashes
        logger.info(f"Extracting archive members to staging '{staging_incoming}'...")
        staged_items: List[Tuple[str, str, str, str]] = []  # (rel_path, staging_file, staging_file_id, target_sha256)

        with ArchiveManager.open_archive(archive_path) as reader:
            for entry in report.entries:
                if entry.is_dir:
                    continue
                rel_path = report.safe_relative_paths.get(entry.path)
                if not rel_path:
                    continue

                staging_dest = os.path.join(staging_incoming, rel_path)
                os.makedirs(os.path.dirname(staging_dest), exist_ok=True)

                with reader.open_member(entry.path) as src, open(staging_dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)

                # Compute identity & hash in staging
                s_vol, s_fid = get_file_identity(staging_dest)
                s_sha = compute_sha256(staging_dest)
                staged_items.append((rel_path, staging_dest, str(s_fid), s_sha))

        # Transition into INSTALLING
        self.state_db.update_transaction_status(tx_id, TransactionStatus.INSTALLING, TransactionStatus.PREPARED)

        # 3. Two-phase installation under LockContext.guard()
        try:
            with lock_context.guard():
                for rel_path, staging_file, staging_file_id, target_sha256 in staged_items:
                    target_file = os.path.join(norm_game_root, rel_path)

                    # Determine if CREATE or OVERWRITE
                    if os.path.exists(target_file):
                        self._apply_overwrite(
                            tx_id=tx_id,
                            rel_path=rel_path,
                            target_file=target_file,
                            staging_file=staging_file,
                            staging_file_id=staging_file_id,
                            target_sha256=target_sha256,
                            tx_backup_dir=staging_backups,
                            lock_context=lock_context
                        )
                    else:
                        self._apply_create(
                            tx_id=tx_id,
                            rel_path=rel_path,
                            target_file=target_file,
                            staging_file=staging_file,
                            staging_file_id=staging_file_id,
                            target_sha256=target_sha256,
                            lock_context=lock_context
                        )

            # All files successfully applied -> COMMIT transaction
            self.state_db.update_transaction_status(tx_id, TransactionStatus.COMMITTED, TransactionStatus.INSTALLING)
            logger.info(f"Transaction '{tx_id}' successfully COMMITTED for DLC '{dlc_code}'.")

            # Clean staging folder after commit
            shutil.rmtree(staging_root, ignore_errors=True)
            return self.state_db.get_transaction(tx_id)

        except Exception as e:
            logger.critical(f"Installation error during tx '{tx_id}': {e}. Initiating rollback...")
            self._rollback_on_failure(tx_id, norm_game_root, lock_context)
            raise

    def _apply_create(
        self,
        tx_id: str,
        rel_path: str,
        target_file: str,
        staging_file: str,
        staging_file_id: str,
        target_sha256: str,
        lock_context: LockContext
    ) -> None:
        """
        Executes CREATE algorithm per Section 7.3.1:
        1. Logs intention: CREATE, PENDING.
        2. Moves staging to target with ReplaceIfExists = FALSE.
        3. Verifies target file.
        4. Updates DB to APPLIED.
        """
        # Step 1: Log intention
        self.state_db.log_file_action(
            tx_id=tx_id,
            relative_path=rel_path,
            action_type=FileActionType.CREATE,
            staging_path=staging_file,
            target_sha256=target_sha256,
            staging_file_id=staging_file_id,
            status=FileLogStatus.PENDING
        )

        # Step 2: Atomic rename with ReplaceIfExists = FALSE
        try:
            move_file_replace_if_exists_false(staging_file, target_file, lock_context)
        except FileExistsError as e:
            # Atomic rejection: target name collision
            logger.error(f"CREATE collision on '{target_file}': target already exists.")
            self.state_db.update_file_status(tx_id, rel_path, FileLogStatus.CONFLICT)
            raise RecoveryConflictError(f"Name collision during CREATE: {e}") from e

        # Step 3: Verify target file
        vol, fid, sha = assert_verified_object(
            target_file,
            expected_file_id=staging_file_id,
            expected_sha256=target_sha256,
            lock_context=lock_context
        )

        # Step 4: Mark APPLIED
        self.state_db.update_file_status(
            tx_id=tx_id,
            relative_path=rel_path,
            new_status=FileLogStatus.APPLIED,
            installed_file_id=str(fid),
            expected_status=FileLogStatus.PENDING
        )
        logger.info(f"CREATE APPLIED: '{rel_path}'")

    def _apply_overwrite(
        self,
        tx_id: str,
        rel_path: str,
        target_file: str,
        staging_file: str,
        staging_file_id: str,
        target_sha256: str,
        tx_backup_dir: str,
        lock_context: LockContext
    ) -> None:
        """
        Executes OVERWRITE algorithm per Section 7.3.2 (Two-phase evacuation):
        1. Reads original FileId and SHA-256.
        2. Logs intention: OVERWRITE, PENDING.
        3. Moves original to tx_backups with ReplaceIfExists = FALSE.
        4. Verifies backup file.
        5. Moves staging file to target with ReplaceIfExists = FALSE.
        6. Verifies installed file.
        7. Marks APPLIED.
        """
        # Step 1: Capture original details
        orig_vol, orig_fid = get_file_identity(target_file)
        orig_sha = compute_sha256(target_file)

        backup_filename = f"bak_{tx_id}_{orig_fid}_{os.path.basename(target_file)}"
        backup_file = os.path.join(tx_backup_dir, backup_filename)

        # Step 2: Log intention
        self.state_db.log_file_action(
            tx_id=tx_id,
            relative_path=rel_path,
            action_type=FileActionType.OVERWRITE,
            staging_path=staging_file,
            backup_path=backup_file,
            original_sha256=orig_sha,
            target_sha256=target_sha256,
            original_file_id=str(orig_fid),
            staging_file_id=staging_file_id,
            status=FileLogStatus.PENDING
        )

        # Step 3: Evacuate original to tx_backups
        move_file_replace_if_exists_false(target_file, backup_file, lock_context)

        # Step 4: Verify backup
        assert_verified_object(
            backup_file,
            expected_volume_serial=orig_vol,
            expected_file_id=str(orig_fid),
            expected_sha256=orig_sha,
            lock_context=lock_context
        )

        # Step 5: Move staging file into vacated target name with ReplaceIfExists = FALSE
        try:
            move_file_replace_if_exists_false(staging_file, target_file, lock_context)
        except FileExistsError as e:
            # Foreign collision: an alien file appeared while target was vacated!
            logger.critical(f"Target path '{target_file}' occupied by foreign file during OVERWRITE!")
            self.state_db.update_file_status(tx_id, rel_path, FileLogStatus.CONFLICT)
            raise RecoveryConflictError(f"Foreign file collision during OVERWRITE: {e}") from e

        # Step 6: Verify installed file
        vol, fid, sha = assert_verified_object(
            target_file,
            expected_file_id=staging_file_id,
            expected_sha256=target_sha256,
            lock_context=lock_context
        )

        # Step 7: Mark APPLIED
        self.state_db.update_file_status(
            tx_id=tx_id,
            relative_path=rel_path,
            new_status=FileLogStatus.APPLIED,
            installed_file_id=str(fid),
            expected_status=FileLogStatus.PENDING
        )
        logger.info(f"OVERWRITE APPLIED: '{rel_path}'")

    def _rollback_on_failure(self, tx_id: str, game_root: str, lock_context: LockContext) -> None:
        """
        Executes atomic rollback of partially applied files in the transaction.
        """
        try:
            self.state_db.update_transaction_status(tx_id, TransactionStatus.ROLLING_BACK)
            recovery_engine = CrashRecoveryEngine(self.state_db, game_root)
            outcome = recovery_engine.recover_transaction(tx_id, lock_context)
            if outcome == TransactionStatus.ABORTED_RECOVERED:
                self.state_db.update_transaction_status(tx_id, TransactionStatus.ROLLED_BACK)
                logger.info(f"Transaction '{tx_id}' successfully ROLLED_BACK.")
            else:
                self.state_db.update_transaction_status(tx_id, TransactionStatus.RECOVERY_CONFLICT)
                logger.critical(f"Rollback of tx '{tx_id}' resulted in conflict: {outcome.value}")
        except Exception as rb_err:
            logger.critical(f"Fatal error during rollback of tx '{tx_id}': {rb_err}")
            try:
                self.state_db.update_transaction_status(tx_id, TransactionStatus.RECOVERY_CONFLICT)
            except Exception:
                pass

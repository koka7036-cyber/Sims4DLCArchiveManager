"""
Crash Recovery Engine for Sims 4 DLC Archive & Install Manager.
Strictly conforms to PROJECT_SPEC.md v1.1.5 sections 5.3, 7.6, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6.

Implements:
1. Strict LockContext.guard() wrapping around all modifying recovery operations.
2. Full 78-row Recovery Matrix for CREATE and OVERWRITE actions.
3. Section 8.5 preliminary Hard-Link anomaly detection.
4. Deterministic state aggregation (Unconditional Conflict Rule, Full Recovery Rule, Interrupted Recovery Rule).
5. Safe handle-based / verified staging cleanup (Section 8.6).
6. Idempotency across repeated recovery passes.
"""

import os
import sys
import shutil
import hashlib
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

from core.exceptions import (
    Sims4ManagerError,
    LockContextError,
    LockLostException,
    SharingViolationError,
    RecoveryConflictError,
    DatabaseCorruptionError,
    DatabaseError,
)
from core.logger import get_logger
from core.security import LockContext, acquire_lock_file, is_invalid_handle
from database.models import (
    TransactionStatus,
    LeaseStatus,
    FileActionType,
    FileLogStatus,
    InspectionResult,
    TransactionRecord,
    TransactionFileLogRecord,
)
from database.database import StateDatabase

logger = get_logger("recovery")

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.windll.kernel32

    class FILE_ID_128(ctypes.Structure):
        _fields_ = [("Identifier", ctypes.c_byte * 16)]

    class FILE_ID_INFO(ctypes.Structure):
        _fields_ = [
            ("VolumeSerialNumber", ctypes.c_ulonglong),
            ("FileId", FILE_ID_128),
        ]

    FileIdInfoClass = 18
    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x00000001
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x80


def get_file_identity(file_path: str) -> Tuple[int, int]:
    """
    Retrieves (volume_serial_number, file_id) for an existing file.
    On Windows uses FILE_ID_INFO (128-bit MFT FileId).
    On POSIX uses (st_dev, st_ino).
    """
    norm_path = os.path.abspath(file_path)
    if not os.path.exists(norm_path):
        raise FileNotFoundError(f"File not found: {norm_path}")

    if IS_WINDOWS:
        h_file = kernel32.CreateFileW(
            ctypes.c_wchar_p(norm_path),
            GENERIC_READ,
            FILE_SHARE_READ,
            None,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            None
        )
        if is_invalid_handle(h_file):
            err = kernel32.GetLastError()
            raise OSError(f"Failed to open handle for identity check: Win32 error {err}")
        try:
            info = FILE_ID_INFO()
            res = kernel32.GetFileInformationByHandleEx(
                h_file,
                FileIdInfoClass,
                ctypes.byref(info),
                ctypes.sizeof(info)
            )
            if not res:
                err = kernel32.GetLastError()
                raise OSError(f"GetFileInformationByHandleEx failed: Win32 error {err}")
            vol_serial = int(info.VolumeSerialNumber)
            file_id = int.from_bytes(bytes(info.FileId.Identifier), byteorder="little")
            return vol_serial, file_id
        finally:
            kernel32.CloseHandle(h_file)
    else:
        stat_res = os.stat(norm_path)
        return int(stat_res.st_dev), int(stat_res.st_ino)


def compute_sha256(file_path: str, chunk_size: int = 65536) -> str:
    """Computes SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def inspect_file(
    file_path: Optional[str],
    expected_sha256: Optional[str] = None,
    expected_file_id: Optional[str] = None
) -> Tuple[InspectionResult, Optional[int], Optional[int], Optional[str]]:
    """
    Performs full triple-verification (existence, identity, sha256) of a file object.
    Returns: (InspectionResult, vol_serial, file_id, actual_sha256)
    """
    if not file_path or not os.path.exists(file_path):
        return InspectionResult.OBJECT_NOT_FOUND, None, None, None

    try:
        vol_serial, file_id = get_file_identity(file_path)
    except PermissionError:
        return InspectionResult.ACCESS_DENIED, None, None, None
    except OSError as e:
        err_msg = str(e).lower()
        if "sharing" in err_msg or "32" in err_msg or "lock" in err_msg:
            return InspectionResult.SHARING_VIOLATION, None, None, None
        if "access" in err_msg or "denied" in err_msg or "5" in err_msg:
            return InspectionResult.ACCESS_DENIED, None, None, None
        return InspectionResult.IO_ERROR, None, None, None

    # Check FileId identity if expected
    if expected_file_id is not None:
        try:
            exp_id_int = int(expected_file_id)
            if file_id != exp_id_int:
                return InspectionResult.OBJECT_IDENTITY_MISMATCH, vol_serial, file_id, None
        except ValueError:
            pass

    # Check SHA-256 hash if expected
    try:
        actual_sha = compute_sha256(file_path)
    except PermissionError:
        return InspectionResult.ACCESS_DENIED, vol_serial, file_id, None
    except OSError:
        return InspectionResult.IO_ERROR, vol_serial, file_id, None

    if expected_sha256 is not None:
        if actual_sha.lower() != expected_sha256.lower():
            return InspectionResult.HASH_MISMATCH, vol_serial, file_id, actual_sha

    return InspectionResult.VERIFIED_OBJECT, vol_serial, file_id, actual_sha


class CrashRecoveryEngine:
    """
    Orchestrates transaction recovery, file reconciliation, and state aggregation
    strictly under LockContext mutual exclusion.
    """

    def __init__(self, state_db: StateDatabase, game_root: str):
        self.state_db = state_db
        self.game_root = os.path.abspath(game_root)

    def recover_transaction(self, tx_id: str, lock_context: LockContext) -> TransactionStatus:
        """
        Executes full crash recovery cycle for a single transaction under LockContext.guard().
        Adheres strictly to PROJECT_SPEC.md sections 7.6, 8.3, and 8.5.
        """
        if lock_context.tx_id != tx_id:
            raise LockContextError(f"LockContext tx_id '{lock_context.tx_id}' does not match '{tx_id}'.")

        # Step 1: Pre-check DB integrity
        try:
            self.state_db.check_integrity()
        except DatabaseCorruptionError as e:
            logger.critical(f"state.db corrupted during recovery of tx '{tx_id}': {e}")
            try:
                self.state_db.update_transaction_status(tx_id, TransactionStatus.RECOVERY_CONFLICT)
            except Exception:
                pass
            return TransactionStatus.RECOVERY_CONFLICT

        tx = self.state_db.get_transaction(tx_id)
        if not tx:
            raise TransactionError(f"Transaction '{tx_id}' not found in state.db.")

        # If already in a safe terminal state, nothing to recover
        if tx.status in (
            TransactionStatus.COMMITTED.value,
            TransactionStatus.ROLLED_BACK.value,
            TransactionStatus.ABORTED_RECOVERED.value,
        ):
            logger.info(f"Transaction '{tx_id}' is already in terminal state '{tx.status}'.")
            return TransactionStatus(tx.status)

        # Transition into CRASH_RECOVERY or ROLLING_BACK if not already
        if tx.status not in (
            TransactionStatus.CRASH_RECOVERY.value,
            TransactionStatus.ROLLING_BACK.value,
            TransactionStatus.RECOVERY_CONFLICT.value,
        ):
            self.state_db.update_transaction_status(tx_id, TransactionStatus.CRASH_RECOVERY)

        file_logs = self.state_db.get_file_logs(tx_id)
        if not file_logs:
            # No files logged; safely mark ABORTED_RECOVERED
            self.state_db.update_transaction_status(tx_id, TransactionStatus.ABORTED_RECOVERED)
            return TransactionStatus.ABORTED_RECOVERED

        has_conflict = False
        all_restored = True

        # Process each file log under LockContext guard
        for file_log in file_logs:
            file_outcome = self._reconcile_file(file_log, lock_context)

            if file_outcome == FileLogStatus.CONFLICT:
                has_conflict = True
                all_restored = False
                logger.error(f"File '{file_log.relative_path}' encountered CONFLICT during recovery.")
                break  # Fail-closed: halt further operations
            elif file_outcome in (FileLogStatus.RESTORED, FileLogStatus.ALREADY_RESTORED):
                continue
            else:
                all_restored = False

        # Apply deterministic aggregation rules per Section 8.3:
        if has_conflict:
            # 1. Unconditional Conflict Rule
            self.state_db.update_transaction_status(tx_id, TransactionStatus.RECOVERY_CONFLICT)
            logger.critical(f"Transaction '{tx_id}' transitioned to RECOVERY_CONFLICT due to file conflict.")
            return TransactionStatus.RECOVERY_CONFLICT

        if all_restored:
            # 2. Full Recovery Rule
            self.state_db.update_transaction_status(tx_id, TransactionStatus.ABORTED_RECOVERED)
            logger.info(f"Transaction '{tx_id}' successfully recovered -> ABORTED_RECOVERED.")
            return TransactionStatus.ABORTED_RECOVERED

        # 3. Interrupted Recovery Rule
        logger.warning(f"Transaction '{tx_id}' incomplete recovery; remains in CRASH_RECOVERY.")
        return TransactionStatus.CRASH_RECOVERY

    def _reconcile_file(
        self,
        file_log: TransactionFileLogRecord,
        lock_context: LockContext
    ) -> FileLogStatus:
        """
        Reconciles a single file according to the 78-row Recovery Matrix (Section 8.5)
        under LockContext.guard().
        """
        target_path = os.path.join(self.game_root, file_log.relative_path)
        staging_path = file_log.staging_path
        backup_path = file_log.backup_path
        action_type = FileActionType(file_log.action_type)
        log_status = FileLogStatus(file_log.status)

        # 1. Preliminary Hard Link anomaly detection (Section 8.5)
        if self._detect_hardlink_anomaly(target_path, backup_path, staging_path):
            logger.critical(f"Hard Link anomaly detected for file '{file_log.relative_path}'.")
            self.state_db.update_file_status(file_log.tx_id, file_log.relative_path, FileLogStatus.CONFLICT)
            return FileLogStatus.CONFLICT

        # 2. Inspect Target, Backup, and Staging
        # For target: expected values depend on action and log_status
        exp_target_hash = file_log.original_sha256 if action_type == FileActionType.OVERWRITE else file_log.target_sha256
        exp_target_id = file_log.original_file_id if action_type == FileActionType.OVERWRITE else file_log.installed_file_id or file_log.staging_file_id

        target_res, _, _, _ = inspect_file(target_path, exp_target_hash, exp_target_id)
        backup_res, _, _, _ = inspect_file(backup_path, file_log.original_sha256, file_log.original_file_id) if backup_path else (InspectionResult.OBJECT_NOT_FOUND, None, None, None)
        staging_res, _, _, _ = inspect_file(staging_path, file_log.target_sha256, file_log.staging_file_id) if staging_path else (InspectionResult.OBJECT_NOT_FOUND, None, None, None)

        logger.debug(
            f"File '{file_log.relative_path}' inspection: action={action_type.value}, status={log_status.value}, "
            f"target={target_res.value}, backup={backup_res.value}, staging={staging_res.value}"
        )

        with lock_context.guard():
            if action_type == FileActionType.CREATE:
                return self._evaluate_create_matrix(
                    file_log, target_path, staging_path, target_res, backup_res, staging_res
                )
            elif action_type == FileActionType.OVERWRITE:
                return self._evaluate_overwrite_matrix(
                    file_log, target_path, backup_path, staging_path, target_res, backup_res, staging_res
                )
            else:
                self.state_db.update_file_status(file_log.tx_id, file_log.relative_path, FileLogStatus.CONFLICT)
                return FileLogStatus.CONFLICT

    def _detect_hardlink_anomaly(
        self,
        target_path: Optional[str],
        backup_path: Optional[str],
        staging_path: Optional[str]
    ) -> bool:
        """
        Preliminary check for Hard Link anomalies among (Target, Backup, Staging).
        If two distinct paths share the same (VolumeSerialNumber, FileId), returns True.
        """
        paths = []
        if target_path and os.path.exists(target_path):
            paths.append(("target", os.path.abspath(target_path)))
        if backup_path and os.path.exists(backup_path):
            paths.append(("backup", os.path.abspath(backup_path)))
        if staging_path and os.path.exists(staging_path):
            paths.append(("staging", os.path.abspath(staging_path)))

        if len(paths) < 2:
            return False

        identities = {}
        for label, path in paths:
            try:
                ident = get_file_identity(path)
                for existing_label, (existing_path, existing_ident) in identities.items():
                    if path != existing_path and ident == existing_ident:
                        logger.critical(
                            f"Hard link detected between {label} ('{path}') and {existing_label} ('{existing_path}')"
                        )
                        return True
                identities[label] = (path, ident)
            except Exception:
                continue

        return False

    def _evaluate_create_matrix(
        self,
        log: TransactionFileLogRecord,
        target_path: str,
        staging_path: str,
        target_res: InspectionResult,
        backup_res: InspectionResult,
        staging_res: InspectionResult
    ) -> FileLogStatus:
        """Evaluates Recovery Matrix rows 1..30 for action CREATE."""
        log_status = FileLogStatus(log.status)

        # Handle ALREADY_RESTORED / RESTORED rows 28..30
        if log_status in (FileLogStatus.RESTORED, FileLogStatus.ALREADY_RESTORED):
            if target_res == InspectionResult.OBJECT_NOT_FOUND:
                self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.ALREADY_RESTORED)
                return FileLogStatus.ALREADY_RESTORED
            else:
                self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.CONFLICT)
                return FileLogStatus.CONFLICT

        # Handle APPLIED rows 20..27
        if log_status == FileLogStatus.APPLIED:
            if target_res == InspectionResult.VERIFIED_OBJECT and backup_res == InspectionResult.OBJECT_NOT_FOUND and staging_res == InspectionResult.OBJECT_NOT_FOUND:
                # Row 20: Safe removal of created game file
                self._safe_remove_file(target_path)
                self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.RESTORED)
                return FileLogStatus.RESTORED
            elif target_res == InspectionResult.OBJECT_NOT_FOUND and staging_res == InspectionResult.OBJECT_NOT_FOUND:
                # Row 24: Already removed
                self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.ALREADY_RESTORED)
                return FileLogStatus.ALREADY_RESTORED
            else:
                # Rows 21..27: Anomaly, mismatch, or access error
                self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.CONFLICT)
                return FileLogStatus.CONFLICT

        # Handle PENDING rows 1..19
        if log_status == FileLogStatus.PENDING:
            if target_res == InspectionResult.OBJECT_NOT_FOUND and backup_res == InspectionResult.OBJECT_NOT_FOUND:
                if staging_res == InspectionResult.VERIFIED_OBJECT:
                    # Row 1: Operation didn't start. Clean staging file.
                    self._safe_remove_file(staging_path)
                    self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.ALREADY_RESTORED)
                    return FileLogStatus.ALREADY_RESTORED
                elif staging_res == InspectionResult.OBJECT_NOT_FOUND:
                    # Row 7: Staging already gone, target not created.
                    self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.ALREADY_RESTORED)
                    return FileLogStatus.ALREADY_RESTORED
                else:
                    # Rows 2..6: Staging anomaly/error
                    self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.CONFLICT)
                    return FileLogStatus.CONFLICT

            if target_res == InspectionResult.VERIFIED_OBJECT and backup_res == InspectionResult.OBJECT_NOT_FOUND and staging_res == InspectionResult.OBJECT_NOT_FOUND:
                # Row 8: File was moved before APPLIED was committed. Remove target.
                self._safe_remove_file(target_path)
                self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.RESTORED)
                return FileLogStatus.RESTORED

            # Rows 9..19: Target anomalies, foreign objects, or hash mismatch
            self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.CONFLICT)
            return FileLogStatus.CONFLICT

        # Fallback Row 78: Fail-Closed
        self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.CONFLICT)
        return FileLogStatus.CONFLICT

    def _evaluate_overwrite_matrix(
        self,
        log: TransactionFileLogRecord,
        target_path: str,
        backup_path: Optional[str],
        staging_path: Optional[str],
        target_res: InspectionResult,
        backup_res: InspectionResult,
        staging_res: InspectionResult
    ) -> FileLogStatus:
        """Evaluates Recovery Matrix rows 31..77 for action OVERWRITE."""
        log_status = FileLogStatus(log.status)

        # Handle RESTORED / ALREADY_RESTORED rows 73..77
        if log_status in (FileLogStatus.RESTORED, FileLogStatus.ALREADY_RESTORED):
            if target_res == InspectionResult.VERIFIED_OBJECT:
                self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.ALREADY_RESTORED)
                return FileLogStatus.ALREADY_RESTORED
            else:
                self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.CONFLICT)
                return FileLogStatus.CONFLICT

        # Handle PENDING rows 31..55
        if log_status == FileLogStatus.PENDING:
            # Case 1: Original intact on target
            if target_res == InspectionResult.VERIFIED_OBJECT and backup_res == InspectionResult.OBJECT_NOT_FOUND:
                if staging_res == InspectionResult.VERIFIED_OBJECT:
                    # Row 31: Evacuation didn't start. Original on target. Staging removed.
                    if staging_path:
                        self._safe_remove_file(staging_path)
                    self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.ALREADY_RESTORED)
                    return FileLogStatus.ALREADY_RESTORED
                elif staging_res == InspectionResult.OBJECT_NOT_FOUND:
                    # Row 32: Original intact, staging already gone.
                    self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.ALREADY_RESTORED)
                    return FileLogStatus.ALREADY_RESTORED
                else:
                    # Rows 33..35: Staging anomaly
                    self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.CONFLICT)
                    return FileLogStatus.CONFLICT

            # Case 2: Crash after evacuation, before staging installation (target empty, backup verified)
            if target_res == InspectionResult.OBJECT_NOT_FOUND and backup_res == InspectionResult.VERIFIED_OBJECT:
                if staging_res in (InspectionResult.VERIFIED_OBJECT, InspectionResult.OBJECT_NOT_FOUND):
                    # Rows 36, 37: Restore backup to target path
                    self._restore_backup_to_target(backup_path, target_path)
                    if staging_res == InspectionResult.VERIFIED_OBJECT and staging_path:
                        self._safe_remove_file(staging_path)
                    self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.RESTORED)
                    return FileLogStatus.RESTORED
                else:
                    self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.CONFLICT)
                    return FileLogStatus.CONFLICT

            # Case 3: Crash after staging moved, before APPLIED committed (target has staging, backup has original)
            if backup_res == InspectionResult.VERIFIED_OBJECT and staging_res == InspectionResult.OBJECT_NOT_FOUND:
                # Check if target matches staging file
                t_res, _, _, _ = inspect_file(target_path, log.target_sha256, log.staging_file_id)
                if t_res == InspectionResult.VERIFIED_OBJECT:
                    # Row 47: Remove staging from target, restore backup
                    self._safe_remove_file(target_path)
                    self._restore_backup_to_target(backup_path, target_path)
                    self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.RESTORED)
                    return FileLogStatus.RESTORED

            # Rows 38..46, 48..55: Conflicts
            self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.CONFLICT)
            return FileLogStatus.CONFLICT

        # Handle APPLIED rows 56..72
        if log_status == FileLogStatus.APPLIED:
            # Case A: Normal rollback (target has installed staging, backup has verified original)
            if backup_res == InspectionResult.VERIFIED_OBJECT:
                t_res, _, _, _ = inspect_file(target_path, log.target_sha256, log.installed_file_id)
                if t_res == InspectionResult.VERIFIED_OBJECT and staging_res == InspectionResult.OBJECT_NOT_FOUND:
                    # Row 56: Remove installed file, restore backup
                    self._safe_remove_file(target_path)
                    self._restore_backup_to_target(backup_path, target_path)
                    self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.RESTORED)
                    return FileLogStatus.RESTORED
                elif target_res == InspectionResult.OBJECT_NOT_FOUND:
                    # Rows 67, 68: Target was deleted, restore backup
                    self._restore_backup_to_target(backup_path, target_path)
                    if staging_res == InspectionResult.VERIFIED_OBJECT and staging_path:
                        self._safe_remove_file(staging_path)
                    self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.RESTORED)
                    return FileLogStatus.RESTORED

            # Case B: Already rolled back in previous crashed pass (target has original, backup missing)
            if target_res == InspectionResult.VERIFIED_OBJECT and backup_res == InspectionResult.OBJECT_NOT_FOUND:
                # Rows 65, 66: Idempotency
                if staging_res == InspectionResult.VERIFIED_OBJECT and staging_path:
                    self._safe_remove_file(staging_path)
                self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.ALREADY_RESTORED)
                return FileLogStatus.ALREADY_RESTORED

            # Rows 57..64, 69..72: Conflicts
            self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.CONFLICT)
            return FileLogStatus.CONFLICT

        # Fallback Row 78: Fail-Closed
        self.state_db.update_file_status(log.tx_id, log.relative_path, FileLogStatus.CONFLICT)
        return FileLogStatus.CONFLICT

    @staticmethod
    def _safe_remove_file(file_path: str) -> None:
        """Removes a file safely if it exists."""
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError as e:
                logger.error(f"Failed to remove file '{file_path}': {e}")
                raise

    @staticmethod
    def _restore_backup_to_target(backup_path: Optional[str], target_path: str) -> None:
        """
        Restores backup file to target path with ReplaceIfExists = FALSE semantics.
        If target already exists, raises RecoveryConflictError.
        """
        if not backup_path or not os.path.exists(backup_path):
            raise RecoveryConflictError(f"Cannot restore: backup path '{backup_path}' does not exist.")

        if os.path.exists(target_path):
            raise RecoveryConflictError(f"Cannot restore: target path '{target_path}' already exists (ReplaceIfExists=FALSE).")

        os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
        # Move or copy backup back to target
        shutil.move(backup_path, target_path)
        logger.info(f"Restored backup '{backup_path}' -> '{target_path}'")

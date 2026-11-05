"""
Unit tests for Stage 6: SQLite WAL + Journal of Operations + Crash Recovery.
Strictly conforms to PROJECT_SPEC.md v1.1.5 sections 3, 5.2, 7.4, 7.6, 8.1, 8.3, 8.4, 8.5, 8.6.
"""

import os
import sys
import uuid
import shutil
import tempfile
import sqlite3
import unittest
import hashlib
from pathlib import Path

from core.exceptions import (
    DatabaseError,
    DatabaseCorruptionError,
    DatabaseCommitError,
    TransactionError,
    LockContextError,
    LockLostException,
    SharingViolationError,
)
from core.security import LockContext, acquire_lock_file, generate_lease_token
from database.models import (
    TransactionStatus,
    LeaseStatus,
    FileActionType,
    FileLogStatus,
    InspectionResult,
    TransactionRecord,
    TransactionLeaseRecord,
    TransactionFileLogRecord,
    DLCItemRecord,
    DownloadHistoryRecord,
)
from database.database import StateDatabase, CatalogDatabase
from database.recovery import (
    CrashRecoveryEngine,
    inspect_file,
    compute_sha256,
    get_file_identity,
)


def _write_file(path: str, content: bytes) -> str:
    """Helper to write binary content to a file, creating parent directories."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return hashlib.sha256(content).hexdigest()


class TestDatabaseWALAndRecovery(unittest.TestCase):
    """Comprehensive test suite for Stage 6 SQLite WAL and Crash Recovery Engine."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_sims4_db_wal_")
        self.state_db_path = os.path.join(self.temp_dir, "database", "state.db")
        self.catalog_db_path = os.path.join(self.temp_dir, "database", "catalog.db")
        self.game_root = os.path.join(self.temp_dir, "game_root")
        self.staging_dir = os.path.join(self.temp_dir, "staging")
        self.backup_dir = os.path.join(self.temp_dir, "tx_backups")
        self.locks_dir = os.path.join(self.temp_dir, "locks")

        for d in (self.game_root, self.staging_dir, self.backup_dir, self.locks_dir):
            os.makedirs(d, exist_ok=True)

        self.state_db = StateDatabase(self.state_db_path)
        self.catalog_db = CatalogDatabase(self.catalog_db_path)
        self.recovery_engine = CrashRecoveryEngine(self.state_db, self.game_root)

    def tearDown(self):
        self.state_db.close()
        self.catalog_db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # --------------------------------------------------------------------------
    # 1. Database Creation & PRAGMA Verification
    # --------------------------------------------------------------------------

    def test_create_new_databases(self):
        """Test that state.db and catalog.db are created with valid schemas."""
        self.assertTrue(os.path.exists(self.state_db_path))
        self.assertTrue(os.path.exists(self.catalog_db_path))
        self.assertTrue(self.state_db.check_integrity())
        self.assertTrue(self.catalog_db.check_integrity())

    def test_wal_mode_verification(self):
        """Test that both state.db and catalog.db operate strictly in WAL mode."""
        state_pragmas = self.state_db.get_pragmas()
        catalog_pragmas = self.catalog_db.get_pragmas()

        self.assertEqual(state_pragmas["journal_mode"], "WAL")
        self.assertEqual(catalog_pragmas["journal_mode"], "WAL")

    def test_pragma_checks(self):
        """
        Test mandatory PRAGMA requirements:
        - state.db: synchronous = FULL (2), foreign_keys = ON (1), busy_timeout = 30000
        - catalog.db: synchronous = NORMAL (1), foreign_keys = ON (1), busy_timeout = 10000
        """
        state_pragmas = self.state_db.get_pragmas()
        self.assertEqual(state_pragmas["synchronous"], 2, "state.db must have synchronous=FULL (2)")
        self.assertEqual(state_pragmas["foreign_keys"], 1, "state.db must enforce foreign keys")
        self.assertEqual(state_pragmas["busy_timeout"], 30000, "state.db timeout must be 30000ms")

        catalog_pragmas = self.catalog_db.get_pragmas()
        self.assertEqual(catalog_pragmas["synchronous"], 1, "catalog.db must have synchronous=NORMAL (1)")
        self.assertEqual(catalog_pragmas["foreign_keys"], 1, "catalog.db must enforce foreign keys")
        self.assertEqual(catalog_pragmas["busy_timeout"], 10000, "catalog.db timeout must be 10000ms")

    def test_full_schema_validation(self):
        """Verify tables, columns, NOT NULL and CHECK constraints."""
        # Test state.db foreign key enforcement
        with self.assertRaises(Exception):
            # Attempt to insert lease without parent transaction
            with self.state_db._lock:
                cur = self.state_db._conn.cursor()
                cur.execute(
                    "INSERT INTO transaction_leases (tx_id, lease_token, owner_pid, status, acquired_at, updated_at) "
                    "VALUES ('nonexistent_tx', 'token', 1234, 'LEASE_ACTIVE', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);"
                )

        # Test state.db CHECK constraint for invalid status
        with self.assertRaises(sqlite3.IntegrityError):
            with self.state_db._lock:
                cur = self.state_db._conn.cursor()
                cur.execute(
                    "INSERT INTO transactions (tx_id, store_id, dlc_code, status) "
                    "VALUES ('tx_invalid_status', 'store_a', 'EP01', 'INVALID_STATUS');"
                )

    # --------------------------------------------------------------------------
    # 2. Transaction FSM & Lifecycle
    # --------------------------------------------------------------------------

    def test_successful_transaction_lifecycle(self):
        """
        Verify standard happy-path transaction lifecycle:
        CREATED -> PREPARED -> INSTALLING -> COMMITTED
        """
        tx_id = f"tx_happy_{uuid.uuid4().hex[:8]}"
        lease_token = generate_lease_token()

        # 1. Create transaction
        tx = self.state_db.create_transaction(
            tx_id=tx_id,
            store_id="store_alpha",
            dlc_code="EP01",
            lease_token=lease_token,
            owner_pid=os.getpid(),
            initial_status=TransactionStatus.CREATED
        )
        self.assertEqual(tx.status, TransactionStatus.CREATED.value)

        # Verify lease is active
        lease = self.state_db.get_lease(tx_id)
        self.assertIsNotNone(lease)
        self.assertEqual(lease.status, LeaseStatus.LEASE_ACTIVE.value)

        # 2. Transition CREATED -> PREPARED
        self.state_db.update_transaction_status(tx_id, TransactionStatus.PREPARED, TransactionStatus.CREATED)
        self.assertEqual(self.state_db.get_transaction(tx_id).status, TransactionStatus.PREPARED.value)

        # 3. Log file action (CREATE PENDING)
        staging_file = os.path.join(self.staging_dir, "EP01", "ClientFullBuild0.package")
        target_sha = _write_file(staging_file, b"Sims 4 EP01 package data")

        file_log = self.state_db.log_file_action(
            tx_id=tx_id,
            relative_path="EP01/ClientFullBuild0.package",
            action_type=FileActionType.CREATE,
            staging_path=staging_file,
            target_sha256=target_sha,
            status=FileLogStatus.PENDING
        )
        self.assertEqual(file_log.status, FileLogStatus.PENDING.value)

        # 4. Transition PREPARED -> INSTALLING
        self.state_db.update_transaction_status(tx_id, TransactionStatus.INSTALLING, TransactionStatus.PREPARED)

        # 5. Apply file action (PENDING -> APPLIED)
        self.state_db.update_file_status(
            tx_id=tx_id,
            relative_path="EP01/ClientFullBuild0.package",
            new_status=FileLogStatus.APPLIED,
            expected_status=FileLogStatus.PENDING
        )

        # 6. Commit transaction
        self.state_db.update_transaction_status(tx_id, TransactionStatus.COMMITTED, TransactionStatus.INSTALLING)
        self.assertEqual(self.state_db.get_transaction(tx_id).status, TransactionStatus.COMMITTED.value)

    def test_interrupted_transaction_detection(self):
        """Test listing active/incomplete transactions upon startup."""
        tx1 = f"tx_active_{uuid.uuid4().hex[:8]}"
        tx2 = f"tx_committed_{uuid.uuid4().hex[:8]}"
        tx3 = f"tx_installing_{uuid.uuid4().hex[:8]}"

        self.state_db.create_transaction(tx1, "store_a", "EP01", generate_lease_token(), os.getpid())
        self.state_db.create_transaction(tx2, "store_a", "EP02", generate_lease_token(), os.getpid())
        self.state_db.create_transaction(tx3, "store_a", "GP01", generate_lease_token(), os.getpid())

        self.state_db.update_transaction_status(tx2, TransactionStatus.COMMITTED)
        self.state_db.update_transaction_status(tx3, TransactionStatus.INSTALLING)

        incomplete = self.state_db.list_active_or_incomplete_transactions("store_a")
        incomplete_ids = [t.tx_id for t in incomplete]

        self.assertIn(tx1, incomplete_ids)
        self.assertIn(tx3, incomplete_ids)
        self.assertNotIn(tx2, incomplete_ids)

    # --------------------------------------------------------------------------
    # 3. Crash Recovery Matrix: CREATE Scenarios
    # --------------------------------------------------------------------------

    def test_recovery_create_pending_staging_cleaned(self):
        """
        Matrix Row 1: CREATE, PENDING, Target=NOT_FOUND, Backup=NOT_FOUND, Staging=VERIFIED.
        Outcome: Staging file deleted, status=ALREADY_RESTORED, tx=ABORTED_RECOVERED.
        """
        tx_id = f"tx_rec_c1_{uuid.uuid4().hex[:8]}"
        lease_token = generate_lease_token()
        self.state_db.create_transaction(tx_id, "store_a", "EP01", lease_token, os.getpid())
        self.state_db.update_transaction_status(tx_id, TransactionStatus.INSTALLING)

        staging_file = os.path.join(self.staging_dir, "EP01", "file.package")
        file_sha = _write_file(staging_file, b"content_a")

        self.state_db.log_file_action(
            tx_id=tx_id,
            relative_path="EP01/file.package",
            action_type=FileActionType.CREATE,
            staging_path=staging_file,
            target_sha256=file_sha,
            status=FileLogStatus.PENDING
        )

        lock_path = os.path.join(self.locks_dir, f"tx_{tx_id}.lock")
        lock_ctx = acquire_lock_file(lock_path, tx_id, lease_token)

        try:
            status = self.recovery_engine.recover_transaction(tx_id, lock_ctx)
            self.assertEqual(status, TransactionStatus.ABORTED_RECOVERED)
            self.assertFalse(os.path.exists(staging_file), "Staging file should have been cleaned")
            f_log = self.state_db.get_file_log(tx_id, "EP01/file.package")
            self.assertEqual(f_log.status, FileLogStatus.ALREADY_RESTORED.value)
        finally:
            lock_ctx.close()

    def test_recovery_create_applied_target_removed(self):
        """
        Matrix Row 20: CREATE, APPLIED, Target=VERIFIED, Backup=NOT_FOUND, Staging=NOT_FOUND.
        Outcome: Created game target removed, status=RESTORED, tx=ABORTED_RECOVERED.
        """
        tx_id = f"tx_rec_c20_{uuid.uuid4().hex[:8]}"
        lease_token = generate_lease_token()
        self.state_db.create_transaction(tx_id, "store_a", "EP01", lease_token, os.getpid())
        self.state_db.update_transaction_status(tx_id, TransactionStatus.INSTALLING)

        target_file = os.path.join(self.game_root, "EP01", "file.package")
        file_sha = _write_file(target_file, b"installed_target_content")
        staging_file = os.path.join(self.staging_dir, "EP01", "file.package")

        self.state_db.log_file_action(
            tx_id=tx_id,
            relative_path="EP01/file.package",
            action_type=FileActionType.CREATE,
            staging_path=staging_file,
            target_sha256=file_sha,
            status=FileLogStatus.APPLIED
        )

        lock_path = os.path.join(self.locks_dir, f"tx_{tx_id}.lock")
        lock_ctx = acquire_lock_file(lock_path, tx_id, lease_token)

        try:
            status = self.recovery_engine.recover_transaction(tx_id, lock_ctx)
            self.assertEqual(status, TransactionStatus.ABORTED_RECOVERED)
            self.assertFalse(os.path.exists(target_file), "Installed target file should be removed")
            f_log = self.state_db.get_file_log(tx_id, "EP01/file.package")
            self.assertEqual(f_log.status, FileLogStatus.RESTORED.value)
        finally:
            lock_ctx.close()

    # --------------------------------------------------------------------------
    # 4. Crash Recovery Matrix: OVERWRITE Scenarios
    # --------------------------------------------------------------------------

    def test_recovery_overwrite_after_evacuation(self):
        """
        Matrix Row 36: OVERWRITE, PENDING, Target=NOT_FOUND, Backup=VERIFIED, Staging=VERIFIED.
        Outcome: Backup restored to target path, staging cleaned, status=RESTORED, tx=ABORTED_RECOVERED.
        """
        tx_id = f"tx_rec_ov36_{uuid.uuid4().hex[:8]}"
        lease_token = generate_lease_token()
        self.state_db.create_transaction(tx_id, "store_a", "EP01", lease_token, os.getpid())
        self.state_db.update_transaction_status(tx_id, TransactionStatus.INSTALLING)

        target_file = os.path.join(self.game_root, "EP01", "original.package")
        backup_file = os.path.join(self.backup_dir, "EP01", "original.package.bak")
        staging_file = os.path.join(self.staging_dir, "EP01", "new.package")

        orig_sha = _write_file(backup_file, b"original game backup data")
        target_sha = _write_file(staging_file, b"new incoming staging data")

        self.state_db.log_file_action(
            tx_id=tx_id,
            relative_path="EP01/original.package",
            action_type=FileActionType.OVERWRITE,
            staging_path=staging_file,
            backup_path=backup_file,
            original_sha256=orig_sha,
            target_sha256=target_sha,
            status=FileLogStatus.PENDING
        )

        lock_path = os.path.join(self.locks_dir, f"tx_{tx_id}.lock")
        lock_ctx = acquire_lock_file(lock_path, tx_id, lease_token)

        try:
            status = self.recovery_engine.recover_transaction(tx_id, lock_ctx)
            self.assertEqual(status, TransactionStatus.ABORTED_RECOVERED)
            self.assertTrue(os.path.exists(target_file), "Original file must be restored to target")
            self.assertEqual(compute_sha256(target_file), orig_sha)
            self.assertFalse(os.path.exists(backup_file), "Backup should have been moved back to target")
            self.assertFalse(os.path.exists(staging_file), "Staging should be cleaned")
            f_log = self.state_db.get_file_log(tx_id, "EP01/original.package")
            self.assertEqual(f_log.status, FileLogStatus.RESTORED.value)
        finally:
            lock_ctx.close()

    def test_recovery_overwrite_applied_rollback(self):
        """
        Matrix Row 56: OVERWRITE, APPLIED, Target=VERIFIED(staging), Backup=VERIFIED(orig), Staging=NOT_FOUND.
        Outcome: Target installed file removed, backup restored to target, status=RESTORED, tx=ABORTED_RECOVERED.
        """
        tx_id = f"tx_rec_ov56_{uuid.uuid4().hex[:8]}"
        lease_token = generate_lease_token()
        self.state_db.create_transaction(tx_id, "store_a", "EP01", lease_token, os.getpid())
        self.state_db.update_transaction_status(tx_id, TransactionStatus.INSTALLING)

        target_file = os.path.join(self.game_root, "EP01", "installed.package")
        backup_file = os.path.join(self.backup_dir, "EP01", "installed.package.bak")
        staging_file = os.path.join(self.staging_dir, "EP01", "installed.package")

        orig_sha = _write_file(backup_file, b"original backup data v1")
        target_sha = _write_file(target_file, b"installed new data v2")

        self.state_db.log_file_action(
            tx_id=tx_id,
            relative_path="EP01/installed.package",
            action_type=FileActionType.OVERWRITE,
            staging_path=staging_file,
            backup_path=backup_file,
            original_sha256=orig_sha,
            target_sha256=target_sha,
            status=FileLogStatus.APPLIED
        )

        lock_path = os.path.join(self.locks_dir, f"tx_{tx_id}.lock")
        lock_ctx = acquire_lock_file(lock_path, tx_id, lease_token)

        try:
            status = self.recovery_engine.recover_transaction(tx_id, lock_ctx)
            self.assertEqual(status, TransactionStatus.ABORTED_RECOVERED)
            self.assertTrue(os.path.exists(target_file))
            self.assertEqual(compute_sha256(target_file), orig_sha, "Original data must be in place")
            f_log = self.state_db.get_file_log(tx_id, "EP01/installed.package")
            self.assertEqual(f_log.status, FileLogStatus.RESTORED.value)
        finally:
            lock_ctx.close()

    # --------------------------------------------------------------------------
    # 5. Idempotency & Repeated Recovery
    # --------------------------------------------------------------------------

    def test_idempotent_repeat_recovery(self):
        """
        Verify that running CrashRecovery a second time on an already recovered
        transaction is strictly idempotent: files receive ALREADY_RESTORED,
        no filesystem changes are made, and status remains ABORTED_RECOVERED.
        """
        tx_id = f"tx_idempotent_{uuid.uuid4().hex[:8]}"
        lease_token = generate_lease_token()
        self.state_db.create_transaction(tx_id, "store_a", "EP01", lease_token, os.getpid())
        self.state_db.update_transaction_status(tx_id, TransactionStatus.INSTALLING)

        target_file = os.path.join(self.game_root, "EP01", "test.package")
        file_sha = _write_file(target_file, b"some created file")
        staging_file = os.path.join(self.staging_dir, "EP01", "test.package")

        self.state_db.log_file_action(
            tx_id=tx_id,
            relative_path="EP01/test.package",
            action_type=FileActionType.CREATE,
            staging_path=staging_file,
            target_sha256=file_sha,
            status=FileLogStatus.APPLIED
        )

        lock_path = os.path.join(self.locks_dir, f"tx_{tx_id}.lock")
        lock_ctx = acquire_lock_file(lock_path, tx_id, lease_token)

        try:
            # First pass: RESTORED
            status1 = self.recovery_engine.recover_transaction(tx_id, lock_ctx)
            self.assertEqual(status1, TransactionStatus.ABORTED_RECOVERED)
            f_log1 = self.state_db.get_file_log(tx_id, "EP01/test.package")
            self.assertEqual(f_log1.status, FileLogStatus.RESTORED.value)

            # Manually reset tx status to CRASH_RECOVERY to simulate another run
            self.state_db.update_transaction_status(tx_id, TransactionStatus.CRASH_RECOVERY)

            # Second pass: ALREADY_RESTORED
            status2 = self.recovery_engine.recover_transaction(tx_id, lock_ctx)
            self.assertEqual(status2, TransactionStatus.ABORTED_RECOVERED)
            f_log2 = self.state_db.get_file_log(tx_id, "EP01/test.package")
            self.assertEqual(f_log2.status, FileLogStatus.ALREADY_RESTORED.value)
        finally:
            lock_ctx.close()

    # --------------------------------------------------------------------------
    # 6. Conflict & Fail-Closed Scenarios
    # --------------------------------------------------------------------------

    def test_error_conflict_on_foreign_target_file(self):
        """
        Matrix Row 13 / 61: Foreign file with unknown hash occupies target path.
        Outcome: Action blocked, status=CONFLICT, tx=RECOVERY_CONFLICT.
        """
        tx_id = f"tx_conflict_foreign_{uuid.uuid4().hex[:8]}"
        lease_token = generate_lease_token()
        self.state_db.create_transaction(tx_id, "store_a", "EP01", lease_token, os.getpid())
        self.state_db.update_transaction_status(tx_id, TransactionStatus.INSTALLING)

        target_file = os.path.join(self.game_root, "EP01", "file.package")
        _write_file(target_file, b"unrecognized foreign user file")

        self.state_db.log_file_action(
            tx_id=tx_id,
            relative_path="EP01/file.package",
            action_type=FileActionType.CREATE,
            staging_path=os.path.join(self.staging_dir, "EP01", "file.package"),
            target_sha256="0000000000000000000000000000000000000000000000000000000000000000",
            status=FileLogStatus.PENDING
        )

        lock_path = os.path.join(self.locks_dir, f"tx_{tx_id}.lock")
        lock_ctx = acquire_lock_file(lock_path, tx_id, lease_token)

        try:
            status = self.recovery_engine.recover_transaction(tx_id, lock_ctx)
            self.assertEqual(status, TransactionStatus.RECOVERY_CONFLICT)
            f_log = self.state_db.get_file_log(tx_id, "EP01/file.package")
            self.assertEqual(f_log.status, FileLogStatus.CONFLICT.value)
            self.assertTrue(os.path.exists(target_file), "Foreign target file must NOT be deleted!")
        finally:
            lock_ctx.close()

    def test_missing_backup_during_overwrite_raises_conflict(self):
        """
        Matrix Row 45 / 59: Target missing or applied, but backup is completely gone.
        Outcome: Cannot restore original, status=CONFLICT, tx=RECOVERY_CONFLICT.
        """
        tx_id = f"tx_missing_backup_{uuid.uuid4().hex[:8]}"
        lease_token = generate_lease_token()
        self.state_db.create_transaction(tx_id, "store_a", "EP01", lease_token, os.getpid())
        self.state_db.update_transaction_status(tx_id, TransactionStatus.INSTALLING)

        target_file = os.path.join(self.game_root, "EP01", "vanished.package")
        backup_file = os.path.join(self.backup_dir, "EP01", "vanished.package.bak")  # Does not exist!

        self.state_db.log_file_action(
            tx_id=tx_id,
            relative_path="EP01/vanished.package",
            action_type=FileActionType.OVERWRITE,
            staging_path=os.path.join(self.staging_dir, "EP01", "vanished.package"),
            backup_path=backup_file,
            original_sha256="1111111111111111111111111111111111111111111111111111111111111111",
            target_sha256="2222222222222222222222222222222222222222222222222222222222222222",
            status=FileLogStatus.PENDING
        )

        lock_path = os.path.join(self.locks_dir, f"tx_{tx_id}.lock")
        lock_ctx = acquire_lock_file(lock_path, tx_id, lease_token)

        try:
            status = self.recovery_engine.recover_transaction(tx_id, lock_ctx)
            self.assertEqual(status, TransactionStatus.RECOVERY_CONFLICT)
            f_log = self.state_db.get_file_log(tx_id, "EP01/vanished.package")
            self.assertEqual(f_log.status, FileLogStatus.CONFLICT.value)
        finally:
            lock_ctx.close()

    def test_corrupted_backup_hash_mismatch(self):
        """
        Matrix Row 38 / 57: Backup file on disk has corrupted bytes (hash mismatch).
        Outcome: Restoration prohibited, status=CONFLICT, tx=RECOVERY_CONFLICT.
        """
        tx_id = f"tx_corrupt_bak_{uuid.uuid4().hex[:8]}"
        lease_token = generate_lease_token()
        self.state_db.create_transaction(tx_id, "store_a", "EP01", lease_token, os.getpid())
        self.state_db.update_transaction_status(tx_id, TransactionStatus.INSTALLING)

        target_file = os.path.join(self.game_root, "EP01", "corrupt.package")
        backup_file = os.path.join(self.backup_dir, "EP01", "corrupt.package.bak")
        _write_file(backup_file, b"corrupted partial backup bytes")

        self.state_db.log_file_action(
            tx_id=tx_id,
            relative_path="EP01/corrupt.package",
            action_type=FileActionType.OVERWRITE,
            staging_path=os.path.join(self.staging_dir, "EP01", "corrupt.package"),
            backup_path=backup_file,
            original_sha256="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            target_sha256="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            status=FileLogStatus.PENDING
        )

        lock_path = os.path.join(self.locks_dir, f"tx_{tx_id}.lock")
        lock_ctx = acquire_lock_file(lock_path, tx_id, lease_token)

        try:
            status = self.recovery_engine.recover_transaction(tx_id, lock_ctx)
            self.assertEqual(status, TransactionStatus.RECOVERY_CONFLICT)
            f_log = self.state_db.get_file_log(tx_id, "EP01/corrupt.package")
            self.assertEqual(f_log.status, FileLogStatus.CONFLICT.value)
        finally:
            lock_ctx.close()

    # --------------------------------------------------------------------------
    # 7. Preliminary Hard-Link Anomaly Detection
    # --------------------------------------------------------------------------

    def test_hardlink_anomaly_detection(self):
        """
        Section 8.5: If target and backup (or staging) share the same physical FileId/inode
        (Hard-Link collision), file modifications must be aborted with CONFLICT.
        """
        tx_id = f"tx_hardlink_{uuid.uuid4().hex[:8]}"
        lease_token = generate_lease_token()
        self.state_db.create_transaction(tx_id, "store_a", "EP01", lease_token, os.getpid())
        self.state_db.update_transaction_status(tx_id, TransactionStatus.INSTALLING)

        target_file = os.path.join(self.game_root, "EP01", "hardlink.package")
        backup_file = os.path.join(self.backup_dir, "EP01", "hardlink.package.bak")

        file_sha = _write_file(target_file, b"data to link")
        os.makedirs(os.path.dirname(backup_file), exist_ok=True)
        os.link(target_file, backup_file)  # Create physical hard link!

        self.state_db.log_file_action(
            tx_id=tx_id,
            relative_path="EP01/hardlink.package",
            action_type=FileActionType.OVERWRITE,
            staging_path=os.path.join(self.staging_dir, "EP01", "hardlink.package"),
            backup_path=backup_file,
            original_sha256=file_sha,
            target_sha256=file_sha,
            status=FileLogStatus.PENDING
        )

        lock_path = os.path.join(self.locks_dir, f"tx_{tx_id}.lock")
        lock_ctx = acquire_lock_file(lock_path, tx_id, lease_token)

        try:
            status = self.recovery_engine.recover_transaction(tx_id, lock_ctx)
            self.assertEqual(status, TransactionStatus.RECOVERY_CONFLICT)
            f_log = self.state_db.get_file_log(tx_id, "EP01/hardlink.package")
            self.assertEqual(f_log.status, FileLogStatus.CONFLICT.value)
        finally:
            lock_ctx.close()

    # --------------------------------------------------------------------------
    # 8. Protocol 8.4: Ambiguous Commit Resolution
    # --------------------------------------------------------------------------

    def test_protocol_8_4_ambiguous_commit(self):
        """
        Section 8.4: Protocol for resolving ambiguous commit:
        - Case A: Record exists with PENDING -> ('PENDING', record)
        - Case B: Record absent -> ('ABSENT', None)
        """
        tx_id = f"tx_proto84_{uuid.uuid4().hex[:8]}"
        self.state_db.create_transaction(tx_id, "store_a", "EP01", generate_lease_token(), os.getpid())

        # Insert record 1
        self.state_db.log_file_action(
            tx_id=tx_id,
            relative_path="EP01/file1.package",
            action_type=FileActionType.CREATE,
            staging_path="/tmp/stage1",
            target_sha256="abc",
            status=FileLogStatus.PENDING
        )

        # Case A: Record exists
        case_a, rec_a = self.state_db.resolve_ambiguous_commit(tx_id, "EP01/file1.package")
        self.assertEqual(case_a, "PENDING")
        self.assertIsNotNone(rec_a)
        self.assertEqual(rec_a.relative_path, "EP01/file1.package")

        # Case B: Record absent
        case_b, rec_b = self.state_db.resolve_ambiguous_commit(tx_id, "EP01/nonexistent.package")
        self.assertEqual(case_b, "ABSENT")
        self.assertIsNone(rec_b)

    # --------------------------------------------------------------------------
    # 9. Database Corruption Handling
    # --------------------------------------------------------------------------

    def test_corrupted_journal_handling(self):
        """
        Rule 8.3.4: If state.db is corrupted, operations fail closed with DatabaseCorruptionError.
        """
        corrupt_db_path = os.path.join(self.temp_dir, "corrupt", "state.db")
        os.makedirs(os.path.dirname(corrupt_db_path), exist_ok=True)
        # Write corrupted header to file
        with open(corrupt_db_path, "wb") as f:
            f.write(b"NOT_A_VALID_SQLITE_DATABASE_HEADER_DATA_GARBAGE")

        with self.assertRaises(DatabaseCorruptionError):
            StateDatabase(corrupt_db_path)

    # --------------------------------------------------------------------------
    # 10. Catalog Database CRUD & Caching
    # --------------------------------------------------------------------------

    def test_catalog_database_operations(self):
        """Test DLC items caching and download history in catalog.db."""
        item = DLCItemRecord(
            dlc_code="EP01",
            name="Get to Work",
            category="Expansion Pack",
            size_bytes=2500000000,
            uncompressed_size_bytes=3200000000,
            sha256="feedbeefcafebabe000000000000000000000000000000000000000000000000",
            download_url="https://disk.yandex.ru/d/example/EP01.zip",
            is_installed=False
        )

        # 1. Upsert DLC
        self.catalog_db.upsert_dlc_item(item)
        fetched = self.catalog_db.get_dlc_item("EP01")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "Get to Work")
        self.assertFalse(fetched.is_installed)

        # 2. Update installed flag
        self.catalog_db.update_dlc_installed_status("EP01", True)
        self.assertTrue(self.catalog_db.get_dlc_item("EP01").is_installed)

        # 3. Add download history
        hist = DownloadHistoryRecord(
            id=0,
            dlc_code="EP01",
            downloaded_bytes=2500000000,
            total_bytes=2500000000,
            status="COMPLETED"
        )
        row_id = self.catalog_db.add_download_history(hist)
        self.assertGreater(row_id, 0)

        history_list = self.catalog_db.get_download_history("EP01")
        self.assertEqual(len(history_list), 1)
        self.assertEqual(history_list[0].status, "COMPLETED")

        # 4. Checkpoint
        busy, log, cp = self.catalog_db.checkpoint("PASSIVE")
        self.assertEqual(busy, 0)


if __name__ == "__main__":
    unittest.main()

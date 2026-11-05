"""
Database connection manager and data access layer for Sims 4 DLC Archive & Install Manager.
Strictly conforms to PROJECT_SPEC.md v1.1.5 sections 3, 5.2, 7.4, 8.1, 8.4.
Provides separate, dedicated connections for state.db (synchronous=FULL) and catalog.db (synchronous=NORMAL).
"""

import os
import sqlite3
import threading
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

from core.exceptions import (
    DatabaseError,
    DatabaseCorruptionError,
    DatabaseCommitError,
    TransactionError,
)
from core.logger import get_logger
from database.models import (
    STATE_DB_SCHEMA,
    CATALOG_DB_SCHEMA,
    TransactionStatus,
    LeaseStatus,
    FileActionType,
    FileLogStatus,
    TransactionRecord,
    TransactionLeaseRecord,
    TransactionFileLogRecord,
    DLCItemRecord,
    DownloadHistoryRecord,
)

logger = get_logger("database")


class StateDatabase:
    """
    Manages the state.db SQLite database.
    Operates strictly in WAL mode with PRAGMA synchronous = FULL for crash resilience
    and atomic intent logging prior to low-level Win32/NT filesystem operations.
    """

    def __init__(self, db_path: str, timeout: float = 30.0):
        self.db_path = str(Path(db_path).resolve())
        self.timeout = timeout
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_connection()

    def _init_connection(self) -> None:
        """Opens SQLite connection and configures mandatory PRAGMAs."""
        parent_dir = os.path.dirname(self.db_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        try:
            self._conn = sqlite3.connect(
                self.db_path,
                timeout=self.timeout,
                check_same_thread=False,
                isolation_level=None  # Explicit autocommit / manual transaction control
            )
            self._conn.row_factory = sqlite3.Row

            cursor = self._conn.cursor()
            # 1. Mandatory WAL mode
            cursor.execute("PRAGMA journal_mode = WAL;")
            row = cursor.fetchone()
            journal_mode = row[0].upper() if row else ""
            if journal_mode != "WAL":
                logger.warning(f"state.db requested WAL mode, got: {journal_mode}")

            # 2. Critical synchronous=FULL for state journal
            cursor.execute("PRAGMA synchronous = FULL;")

            # 3. Enable foreign keys enforcement
            cursor.execute("PRAGMA foreign_keys = ON;")

            # 4. Busy timeout 30000ms
            cursor.execute(f"PRAGMA busy_timeout = {int(self.timeout * 1000)};")

            # Initialize schema
            cursor.executescript(STATE_DB_SCHEMA)

        except sqlite3.DatabaseError as e:
            if "database disk image is malformed" in str(e).lower() or "file is not a database" in str(e).lower():
                raise DatabaseCorruptionError(f"state.db corruption detected: {e}") from e
            raise DatabaseError(f"Failed to initialize state.db: {e}") from e

    def get_pragmas(self) -> Dict[str, Any]:
        """Queries and returns currently active PRAGMA settings."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("PRAGMA journal_mode;")
            jm = cur.fetchone()[0]
            cur.execute("PRAGMA synchronous;")
            sync = cur.fetchone()[0]
            cur.execute("PRAGMA foreign_keys;")
            fk = cur.fetchone()[0]
            cur.execute("PRAGMA busy_timeout;")
            bt = cur.fetchone()[0]
            return {
                "journal_mode": str(jm).upper(),
                "synchronous": int(sync),  # 2 corresponds to FULL
                "foreign_keys": int(fk),
                "busy_timeout": int(bt),
            }

    def check_integrity(self) -> bool:
        """
        Runs PRAGMA schema_version and PRAGMA quick_check to verify database health.
        Raises DatabaseCorruptionError if the database is corrupt or unreadable.
        """
        with self._lock:
            try:
                cur = self._conn.cursor()
                cur.execute("PRAGMA schema_version;")
                _ = cur.fetchone()

                cur.execute("PRAGMA quick_check;")
                rows = cur.fetchall()
                if not rows or rows[0][0].lower() != "ok":
                    msg = "; ".join([str(r[0]) for r in rows])
                    logger.critical(f"state.db quick_check failed: {msg}")
                    raise DatabaseCorruptionError(f"state.db integrity check failed: {msg}")
                return True
            except sqlite3.DatabaseError as e:
                raise DatabaseCorruptionError(f"state.db is corrupt or inaccessible: {e}") from e

    def checkpoint(self, mode: str = "TRUNCATE") -> Tuple[int, int, int]:
        """
        Executes PRAGMA wal_checkpoint(mode).
        Returns (busy, log, checkpointed).
        """
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(f"PRAGMA wal_checkpoint({mode});")
            res = cur.fetchone()
            return (int(res[0]), int(res[1]), int(res[2])) if res else (0, 0, 0)

    def close(self) -> None:
        """Safely closes the database connection."""
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception as e:
                    logger.debug(f"Error closing state.db connection: {e}")
                finally:
                    self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ==========================================================================
    # Protocol 8.4: Ambiguous SQLite COMMIT Resolution
    # ==========================================================================

    def resolve_ambiguous_commit(self, tx_id: str, relative_path: str) -> Tuple[str, Optional[TransactionFileLogRecord]]:
        """
        Implements Protocol 8.4 for ambiguous SQLite commit or crash during WAL write:
        1. Close connection (finalizing statements).
        2. Reopen connection (triggering automatic SQLite WAL recovery).
        3. Run PRAGMA quick_check.
        4. Inspect transaction_file_log:
           - Case A: Record exists with PENDING -> ('PENDING', record)
           - Case B: Record absent and DB intact -> ('ABSENT', None)
           - Case C: Ambiguous or error -> raises DatabaseCorruptionError
        """
        with self._lock:
            self.close()
            try:
                self._init_connection()
                self.check_integrity()
                record = self.get_file_log(tx_id, relative_path)
                if record is not None:
                    return (record.status, record)
                else:
                    return ("ABSENT", None)
            except Exception as e:
                logger.critical(f"Protocol 8.4 ambiguous commit resolution failed: {e}")
                raise DatabaseCorruptionError(f"Failed to safely resolve commit: {e}") from e

    # ==========================================================================
    # Transaction FSM & Lease Operations
    # ==========================================================================

    def create_transaction(
        self,
        tx_id: str,
        store_id: str,
        dlc_code: str,
        lease_token: str,
        owner_pid: int,
        initial_status: TransactionStatus = TransactionStatus.CREATED,
    ) -> TransactionRecord:
        """
        Atomically creates a new transaction record and associated initial active lease.
        """
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE;")
                cur.execute(
                    """
                    INSERT INTO transactions (tx_id, store_id, dlc_code, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                    """,
                    (tx_id, store_id, dlc_code, initial_status.value)
                )
                cur.execute(
                    """
                    INSERT INTO transaction_leases (tx_id, lease_token, owner_pid, status, handoff_hash, acquired_at, updated_at)
                    VALUES (?, ?, ?, 'LEASE_ACTIVE', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
                    """,
                    (tx_id, lease_token, owner_pid)
                )
                cur.execute("COMMIT;")
                logger.info(f"Created transaction '{tx_id}' for DLC '{dlc_code}' (lease={lease_token[:8]}...)")
                return self.get_transaction(tx_id)
            except sqlite3.IntegrityError as e:
                cur.execute("ROLLBACK;")
                raise TransactionError(f"Transaction or lease creation failed (integrity error): {e}") from e
            except Exception as e:
                cur.execute("ROLLBACK;")
                raise DatabaseError(f"Failed to create transaction: {e}") from e

    def get_transaction(self, tx_id: str) -> Optional[TransactionRecord]:
        """Retrieves a single transaction by its tx_id."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT tx_id, store_id, dlc_code, status, created_at, updated_at FROM transactions WHERE tx_id = ?;",
                (tx_id,)
            )
            row = cur.fetchone()
            if not row:
                return None
            return TransactionRecord(
                tx_id=row["tx_id"],
                store_id=row["store_id"],
                dlc_code=row["dlc_code"],
                status=row["status"],
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"])
            )

    def update_transaction_status(
        self,
        tx_id: str,
        new_status: TransactionStatus,
        expected_status: Optional[TransactionStatus] = None
    ) -> None:
        """
        Conditionally updates the transaction FSM status.
        Enforces rowcount == 1 requirement mandated by PROJECT_SPEC.md.
        """
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE;")
                if expected_status is not None:
                    cur.execute(
                        """
                        UPDATE transactions
                        SET status = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE tx_id = ? AND status = ?;
                        """,
                        (new_status.value, tx_id, expected_status.value)
                    )
                else:
                    cur.execute(
                        """
                        UPDATE transactions
                        SET status = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE tx_id = ?;
                        """,
                        (new_status.value, tx_id)
                    )
                if cur.rowcount != 1:
                    cur.execute("ROLLBACK;")
                    raise TransactionError(
                        f"Failed conditional transition for tx '{tx_id}' to '{new_status.value}': "
                        f"expected status '{expected_status.value if expected_status else 'ANY'}', rowcount={cur.rowcount}"
                    )
                cur.execute("COMMIT;")
                logger.info(f"Updated tx '{tx_id}' status -> {new_status.value}")
            except Exception as e:
                try:
                    cur.execute("ROLLBACK;")
                except Exception:
                    pass
                if isinstance(e, TransactionError):
                    raise
                raise DatabaseError(f"Database error updating transaction status: {e}") from e

    def list_active_or_incomplete_transactions(self, store_id: Optional[str] = None) -> List[TransactionRecord]:
        """
        Returns all transactions currently in an active or non-terminal state,
        requiring crash inspection or recovery upon application startup.
        """
        terminal_statuses = (
            TransactionStatus.COMMITTED.value,
            TransactionStatus.ROLLED_BACK.value,
            TransactionStatus.ABORTED_RECOVERED.value,
        )
        with self._lock:
            cur = self._conn.cursor()
            placeholders = ",".join(["?"] * len(terminal_statuses))
            query = f"SELECT tx_id, store_id, dlc_code, status, created_at, updated_at FROM transactions WHERE status NOT IN ({placeholders})"
            params = list(terminal_statuses)
            if store_id:
                query += " AND store_id = ?"
                params.append(store_id)
            query += " ORDER BY created_at ASC;"

            cur.execute(query, params)
            rows = cur.fetchall()
            return [
                TransactionRecord(
                    tx_id=r["tx_id"],
                    store_id=r["store_id"],
                    dlc_code=r["dlc_code"],
                    status=r["status"],
                    created_at=str(r["created_at"]),
                    updated_at=str(r["updated_at"])
                )
                for r in rows
            ]

    def get_lease(self, tx_id: str) -> Optional[TransactionLeaseRecord]:
        """Retrieves lease authorization record for the specified tx_id."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT tx_id, lease_token, owner_pid, status, handoff_hash, acquired_at, updated_at
                FROM transaction_leases WHERE tx_id = ?;
                """,
                (tx_id,)
            )
            row = cur.fetchone()
            if not row:
                return None
            return TransactionLeaseRecord(
                tx_id=row["tx_id"],
                lease_token=row["lease_token"],
                owner_pid=row["owner_pid"],
                status=row["status"],
                handoff_hash=row["handoff_hash"],
                acquired_at=str(row["acquired_at"]),
                updated_at=str(row["updated_at"])
            )

    def update_lease(
        self,
        tx_id: str,
        new_status: LeaseStatus,
        new_lease_token: Optional[str] = None,
        new_owner_pid: Optional[int] = None,
        handoff_hash: Optional[str] = None,
        expected_status: Optional[LeaseStatus] = None,
        expected_token: Optional[str] = None
    ) -> None:
        """
        Updates the transaction lease record with conditional validation.
        Enforces rowcount == 1 requirement.
        """
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE;")
                where_clauses = ["tx_id = ?"]
                params = [new_status.value]

                set_clauses = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
                if new_lease_token is not None:
                    set_clauses.append("lease_token = ?")
                    params.append(new_lease_token)
                if new_owner_pid is not None:
                    set_clauses.append("owner_pid = ?")
                    params.append(new_owner_pid)
                set_clauses.append("handoff_hash = ?")
                params.append(handoff_hash)

                params.append(tx_id)

                if expected_status is not None:
                    where_clauses.append("status = ?")
                    params.append(expected_status.value)
                if expected_token is not None:
                    where_clauses.append("lease_token = ?")
                    params.append(expected_token)

                sql = f"UPDATE transaction_leases SET {', '.join(set_clauses)} WHERE {' AND '.join(where_clauses)};"
                cur.execute(sql, params)

                if cur.rowcount != 1:
                    cur.execute("ROLLBACK;")
                    raise TransactionError(
                        f"Failed lease update for tx '{tx_id}' to status '{new_status.value}': rowcount={cur.rowcount}"
                    )
                cur.execute("COMMIT;")
                logger.info(f"Updated lease for tx '{tx_id}' -> {new_status.value}")
            except Exception as e:
                try:
                    cur.execute("ROLLBACK;")
                except Exception:
                    pass
                if isinstance(e, TransactionError):
                    raise
                raise DatabaseError(f"Database error updating lease: {e}") from e

    # ==========================================================================
    # File Operations Journal
    # ==========================================================================

    def log_file_action(
        self,
        tx_id: str,
        relative_path: str,
        action_type: FileActionType,
        staging_path: str,
        target_sha256: str,
        original_sha256: Optional[str] = None,
        backup_path: Optional[str] = None,
        original_file_id: Optional[str] = None,
        staging_file_id: Optional[str] = None,
        installed_file_id: Optional[str] = None,
        status: FileLogStatus = FileLogStatus.PENDING
    ) -> TransactionFileLogRecord:
        """
        Records file intent into transaction_file_log before filesystem modification.
        Committed with PRAGMA synchronous=FULL.
        """
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE;")
                cur.execute(
                    """
                    INSERT INTO transaction_file_log (
                        tx_id, relative_path, action_type, staging_path, backup_path,
                        original_sha256, target_sha256, original_file_id,
                        staging_file_id, installed_file_id, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    ON CONFLICT(tx_id, relative_path) DO UPDATE SET
                        action_type = excluded.action_type,
                        staging_path = excluded.staging_path,
                        backup_path = excluded.backup_path,
                        original_sha256 = excluded.original_sha256,
                        target_sha256 = excluded.target_sha256,
                        original_file_id = excluded.original_file_id,
                        staging_file_id = excluded.staging_file_id,
                        installed_file_id = excluded.installed_file_id,
                        status = excluded.status,
                        updated_at = CURRENT_TIMESTAMP;
                    """,
                    (
                        tx_id,
                        relative_path,
                        action_type.value,
                        staging_path,
                        backup_path,
                        original_sha256,
                        target_sha256,
                        original_file_id,
                        staging_file_id,
                        installed_file_id,
                        status.value
                    )
                )
                cur.execute("COMMIT;")
                return self.get_file_log(tx_id, relative_path)
            except Exception as e:
                cur.execute("ROLLBACK;")
                raise DatabaseCommitError(f"Failed to log file action: {e}") from e

    def update_file_status(
        self,
        tx_id: str,
        relative_path: str,
        new_status: FileLogStatus,
        installed_file_id: Optional[str] = None,
        expected_status: Optional[FileLogStatus] = None
    ) -> None:
        """
        Updates the execution status of a file operation in transaction_file_log.
        """
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE;")
                set_clauses = ["status = ?", "updated_at = CURRENT_TIMESTAMP"]
                params = [new_status.value]
                if installed_file_id is not None:
                    set_clauses.append("installed_file_id = ?")
                    params.append(installed_file_id)

                where_clauses = ["tx_id = ?", "relative_path = ?"]
                params.extend([tx_id, relative_path])

                if expected_status is not None:
                    where_clauses.append("status = ?")
                    params.append(expected_status.value)

                sql = f"UPDATE transaction_file_log SET {', '.join(set_clauses)} WHERE {' AND '.join(where_clauses)};"
                cur.execute(sql, params)

                if cur.rowcount != 1:
                    cur.execute("ROLLBACK;")
                    raise TransactionError(
                        f"Failed to update file status for '{relative_path}' in tx '{tx_id}': rowcount={cur.rowcount}"
                    )
                cur.execute("COMMIT;")
            except Exception as e:
                try:
                    cur.execute("ROLLBACK;")
                except Exception:
                    pass
                if isinstance(e, TransactionError):
                    raise
                raise DatabaseError(f"Database error updating file status: {e}") from e

    def get_file_log(self, tx_id: str, relative_path: str) -> Optional[TransactionFileLogRecord]:
        """Retrieves a single file log record by tx_id and relative_path."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT id, tx_id, relative_path, action_type, staging_path, backup_path,
                       original_sha256, target_sha256, original_file_id, staging_file_id,
                       installed_file_id, status, created_at, updated_at
                FROM transaction_file_log WHERE tx_id = ? AND relative_path = ?;
                """,
                (tx_id, relative_path)
            )
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_file_record(row)

    def get_file_logs(self, tx_id: str) -> List[TransactionFileLogRecord]:
        """Retrieves all file log entries for a given transaction."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT id, tx_id, relative_path, action_type, staging_path, backup_path,
                       original_sha256, target_sha256, original_file_id, staging_file_id,
                       installed_file_id, status, created_at, updated_at
                FROM transaction_file_log WHERE tx_id = ? ORDER BY id ASC;
                """,
                (tx_id,)
            )
            rows = cur.fetchall()
            return [self._row_to_file_record(r) for r in rows]

    @staticmethod
    def _row_to_file_record(row: sqlite3.Row) -> TransactionFileLogRecord:
        return TransactionFileLogRecord(
            id=row["id"],
            tx_id=row["tx_id"],
            relative_path=row["relative_path"],
            action_type=row["action_type"],
            staging_path=row["staging_path"],
            backup_path=row["backup_path"],
            original_sha256=row["original_sha256"],
            target_sha256=row["target_sha256"],
            original_file_id=row["original_file_id"],
            staging_file_id=row["staging_file_id"],
            installed_file_id=row["installed_file_id"],
            status=row["status"],
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"])
        )


# ==============================================================================
# Catalog Database Connection Manager
# ==============================================================================

class CatalogDatabase:
    """
    Manages the catalog.db SQLite database.
    Operates in WAL mode with PRAGMA synchronous = NORMAL for high-performance
    caching of cloud DLC manifests, metadata, and download history.
    """

    def __init__(self, db_path: str, timeout: float = 10.0):
        self.db_path = str(Path(db_path).resolve())
        self.timeout = timeout
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_connection()

    def _init_connection(self) -> None:
        """Opens SQLite connection and configures mandatory PRAGMAs."""
        parent_dir = os.path.dirname(self.db_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        try:
            self._conn = sqlite3.connect(
                self.db_path,
                timeout=self.timeout,
                check_same_thread=False,
                isolation_level=None
            )
            self._conn.row_factory = sqlite3.Row

            cursor = self._conn.cursor()
            # 1. WAL mode
            cursor.execute("PRAGMA journal_mode = WAL;")
            row = cursor.fetchone()
            journal_mode = row[0].upper() if row else ""
            if journal_mode != "WAL":
                logger.warning(f"catalog.db requested WAL mode, got: {journal_mode}")

            # 2. Synchronous=NORMAL for high-performance cache
            cursor.execute("PRAGMA synchronous = NORMAL;")

            # 3. Foreign keys
            cursor.execute("PRAGMA foreign_keys = ON;")

            # 4. Busy timeout 10000ms
            cursor.execute(f"PRAGMA busy_timeout = {int(self.timeout * 1000)};")

            # Initialize schema
            cursor.executescript(CATALOG_DB_SCHEMA)

        except sqlite3.DatabaseError as e:
            if "database disk image is malformed" in str(e).lower() or "file is not a database" in str(e).lower():
                raise DatabaseCorruptionError(f"catalog.db corruption detected: {e}") from e
            raise DatabaseError(f"Failed to initialize catalog.db: {e}") from e

    def get_pragmas(self) -> Dict[str, Any]:
        """Queries and returns currently active PRAGMA settings."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("PRAGMA journal_mode;")
            jm = cur.fetchone()[0]
            cur.execute("PRAGMA synchronous;")
            sync = cur.fetchone()[0]
            cur.execute("PRAGMA foreign_keys;")
            fk = cur.fetchone()[0]
            cur.execute("PRAGMA busy_timeout;")
            bt = cur.fetchone()[0]
            return {
                "journal_mode": str(jm).upper(),
                "synchronous": int(sync),  # 1 corresponds to NORMAL
                "foreign_keys": int(fk),
                "busy_timeout": int(bt),
            }

    def check_integrity(self) -> bool:
        """Runs quick integrity check on catalog.db."""
        with self._lock:
            try:
                cur = self._conn.cursor()
                cur.execute("PRAGMA quick_check;")
                rows = cur.fetchall()
                if not rows or rows[0][0].lower() != "ok":
                    msg = "; ".join([str(r[0]) for r in rows])
                    raise DatabaseCorruptionError(f"catalog.db integrity check failed: {msg}")
                return True
            except sqlite3.DatabaseError as e:
                raise DatabaseCorruptionError(f"catalog.db is corrupt: {e}") from e

    def checkpoint(self, mode: str = "PASSIVE") -> Tuple[int, int, int]:
        """Executes PRAGMA wal_checkpoint(mode)."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(f"PRAGMA wal_checkpoint({mode});")
            res = cur.fetchone()
            return (int(res[0]), int(res[1]), int(res[2])) if res else (0, 0, 0)

    def close(self) -> None:
        """Safely closes catalog.db connection."""
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception as e:
                    logger.debug(f"Error closing catalog.db connection: {e}")
                finally:
                    self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ==========================================================================
    # Catalog & Download Operations
    # ==========================================================================

    def upsert_dlc_item(self, item: DLCItemRecord) -> None:
        """Inserts or updates DLC metadata in catalog cache."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO dlc_items (
                    dlc_code, name, category, size_bytes, uncompressed_size_bytes,
                    sha256, md5, download_url, is_installed, last_scanned_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dlc_code) DO UPDATE SET
                    name = excluded.name,
                    category = excluded.category,
                    size_bytes = excluded.size_bytes,
                    uncompressed_size_bytes = excluded.uncompressed_size_bytes,
                    sha256 = coalesce(excluded.sha256, dlc_items.sha256),
                    md5 = coalesce(excluded.md5, dlc_items.md5),
                    download_url = coalesce(excluded.download_url, dlc_items.download_url),
                    is_installed = excluded.is_installed,
                    last_scanned_at = coalesce(excluded.last_scanned_at, dlc_items.last_scanned_at);
                """,
                (
                    item.dlc_code,
                    item.name,
                    item.category,
                    item.size_bytes,
                    item.uncompressed_size_bytes,
                    item.sha256,
                    item.md5,
                    item.download_url,
                    1 if item.is_installed else 0,
                    item.last_scanned_at
                )
            )

    def get_dlc_item(self, dlc_code: str) -> Optional[DLCItemRecord]:
        """Retrieves a single DLC item from catalog."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT dlc_code, name, category, size_bytes, uncompressed_size_bytes,
                       sha256, md5, download_url, is_installed, last_scanned_at
                FROM dlc_items WHERE dlc_code = ?;
                """,
                (dlc_code,)
            )
            row = cur.fetchone()
            if not row:
                return None
            return DLCItemRecord(
                dlc_code=row["dlc_code"],
                name=row["name"],
                category=row["category"],
                size_bytes=row["size_bytes"],
                uncompressed_size_bytes=row["uncompressed_size_bytes"],
                sha256=row["sha256"],
                md5=row["md5"],
                download_url=row["download_url"],
                is_installed=bool(row["is_installed"]),
                last_scanned_at=str(row["last_scanned_at"]) if row["last_scanned_at"] else None
            )

    def list_dlc_items(
        self,
        category: Optional[str] = None,
        installed_only: bool = False
    ) -> List[DLCItemRecord]:
        """Lists DLC items from catalog with optional filters."""
        with self._lock:
            cur = self._conn.cursor()
            query = "SELECT dlc_code, name, category, size_bytes, uncompressed_size_bytes, sha256, md5, download_url, is_installed, last_scanned_at FROM dlc_items"
            where = []
            params = []
            if category:
                where.append("category = ?")
                params.append(category)
            if installed_only:
                where.append("is_installed = 1")

            if where:
                query += " WHERE " + " AND ".join(where)
            query += " ORDER BY dlc_code ASC;"

            cur.execute(query, params)
            rows = cur.fetchall()
            return [
                DLCItemRecord(
                    dlc_code=r["dlc_code"],
                    name=r["name"],
                    category=r["category"],
                    size_bytes=r["size_bytes"],
                    uncompressed_size_bytes=r["uncompressed_size_bytes"],
                    sha256=r["sha256"],
                    md5=r["md5"],
                    download_url=r["download_url"],
                    is_installed=bool(r["is_installed"]),
                    last_scanned_at=str(r["last_scanned_at"]) if r["last_scanned_at"] else None
                )
                for r in rows
            ]

    def update_dlc_installed_status(self, dlc_code: str, is_installed: bool) -> None:
        """Updates installed flag for a DLC."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE dlc_items SET is_installed = ?, last_scanned_at = CURRENT_TIMESTAMP WHERE dlc_code = ?;",
                (1 if is_installed else 0, dlc_code)
            )

    def add_download_history(self, history: DownloadHistoryRecord) -> int:
        """Records a download history event."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO download_history (dlc_code, downloaded_bytes, total_bytes, status, completed_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP);
                """,
                (history.dlc_code, history.downloaded_bytes, history.total_bytes, history.status)
            )
            return cur.lastrowid

    def get_download_history(self, dlc_code: str) -> List[DownloadHistoryRecord]:
        """Retrieves download history for a DLC item."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT id, dlc_code, downloaded_bytes, total_bytes, status, completed_at
                FROM download_history WHERE dlc_code = ? ORDER BY id DESC;
                """,
                (dlc_code,)
            )
            rows = cur.fetchall()
            return [
                DownloadHistoryRecord(
                    id=r["id"],
                    dlc_code=r["dlc_code"],
                    downloaded_bytes=r["downloaded_bytes"],
                    total_bytes=r["total_bytes"],
                    status=r["status"],
                    completed_at=str(r["completed_at"]) if r["completed_at"] else None
                )
                for r in rows
            ]

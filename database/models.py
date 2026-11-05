"""
Database schemas, models, records, and status definitions for Sims 4 DLC Archive & Install Manager.
Strictly adheres to PROJECT_SPEC.md v1.1.5 sections 3, 5.2, 7.4, 8.1, 8.2, 8.3, 8.5.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ==============================================================================
# FSM & Operation Status Enums
# ==============================================================================

class TransactionStatus(str, Enum):
    """
    Finite State Machine (FSM) statuses for high-level DLC transactions.
    Strictly defined by PROJECT_SPEC.md section 8.1.
    """
    CREATED = "CREATED"
    PREPARED = "PREPARED"
    BACKUP_CREATED = "BACKUP_CREATED"
    INSTALLING = "INSTALLING"
    SUSPENDED_UAC_REQUIRED = "SUSPENDED_UAC_REQUIRED"
    ABORTED_ELEVATION_REJECTED = "ABORTED_ELEVATION_REJECTED"
    INSTALLED = "INSTALLED"
    COMMITTED = "COMMITTED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    CRASH_RECOVERY = "CRASH_RECOVERY"
    ABORTED_RECOVERED = "ABORTED_RECOVERED"
    RECOVERY_CONFLICT = "RECOVERY_CONFLICT"


class LeaseStatus(str, Enum):
    """
    Transaction lease authorization statuses in transaction_leases.
    Strictly segregated from FSM transaction statuses (PROJECT_SPEC.md 7.4).
    """
    LEASE_ACTIVE = "LEASE_ACTIVE"
    LEASE_SUSPENDED_UAC = "LEASE_SUSPENDED_UAC"
    LEASE_EXPIRED_UAC_REJECTED = "LEASE_EXPIRED_UAC_REJECTED"
    LEASE_TERMINATED = "LEASE_TERMINATED"


class FileActionType(str, Enum):
    """File operation intent types in transaction_file_log."""
    CREATE = "CREATE"
    OVERWRITE = "OVERWRITE"


class FileLogStatus(str, Enum):
    """
    Status of an individual file operation in transaction_file_log.
    Strictly defined by PROJECT_SPEC.md sections 8.1 and 8.2.
    """
    PENDING = "PENDING"
    APPLIED = "APPLIED"
    RESTORED = "RESTORED"
    ALREADY_RESTORED = "ALREADY_RESTORED"
    CONFLICT = "CONFLICT"


class InspectionResult(str, Enum):
    """
    Result of low-level object inspection for target, backup, and staging paths
    under LockContext guard (PROJECT_SPEC.md section 8.5).
    """
    VERIFIED_OBJECT = "VERIFIED_OBJECT"
    OBJECT_NOT_FOUND = "OBJECT_NOT_FOUND"
    OBJECT_IDENTITY_MISMATCH = "OBJECT_IDENTITY_MISMATCH"
    HASH_MISMATCH = "HASH_MISMATCH"
    ACCESS_DENIED = "ACCESS_DENIED"
    SHARING_VIOLATION = "SHARING_VIOLATION"
    IO_ERROR = "IO_ERROR"


# ==============================================================================
# Record Dataclasses
# ==============================================================================

@dataclass
class TransactionRecord:
    """Represents a row in the state.db `transactions` table."""
    tx_id: str
    store_id: str
    dlc_code: str
    status: str
    created_at: str
    updated_at: str


@dataclass
class TransactionLeaseRecord:
    """Represents a row in the state.db `transaction_leases` table."""
    tx_id: str
    lease_token: str
    owner_pid: int
    status: str
    handoff_hash: Optional[str]
    acquired_at: str
    updated_at: str


@dataclass
class TransactionFileLogRecord:
    """Represents a row in the state.db `transaction_file_log` table."""
    id: int
    tx_id: str
    relative_path: str
    action_type: str
    staging_path: str
    backup_path: Optional[str]
    original_sha256: Optional[str]
    target_sha256: str
    original_file_id: Optional[str]
    staging_file_id: Optional[str]
    installed_file_id: Optional[str]
    status: str
    created_at: str
    updated_at: str


@dataclass
class DLCItemRecord:
    """Represents a row in the catalog.db `dlc_items` table."""
    dlc_code: str
    name: str
    category: str
    size_bytes: int
    uncompressed_size_bytes: int
    sha256: Optional[str] = None
    md5: Optional[str] = None
    download_url: Optional[str] = None
    is_installed: bool = False
    last_scanned_at: Optional[str] = None


@dataclass
class DownloadHistoryRecord:
    """Represents a row in the catalog.db `download_history` table."""
    id: int
    dlc_code: str
    downloaded_bytes: int
    total_bytes: int
    status: str
    completed_at: Optional[str] = None


# ==============================================================================
# SQLite DDL Schemas
# ==============================================================================

STATE_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    tx_id TEXT PRIMARY KEY,
    store_id TEXT NOT NULL,
    dlc_code TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'CREATED', 'PREPARED', 'BACKUP_CREATED', 'INSTALLING',
        'SUSPENDED_UAC_REQUIRED', 'ABORTED_ELEVATION_REJECTED',
        'INSTALLED', 'COMMITTED', 'ROLLING_BACK', 'ROLLED_BACK',
        'CRASH_RECOVERY', 'ABORTED_RECOVERED', 'RECOVERY_CONFLICT'
    )),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transaction_leases (
    tx_id TEXT PRIMARY KEY,
    lease_token TEXT NOT NULL,
    owner_pid INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'LEASE_ACTIVE', 'LEASE_SUSPENDED_UAC',
        'LEASE_EXPIRED_UAC_REJECTED', 'LEASE_TERMINATED'
    )),
    handoff_hash TEXT,
    acquired_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    FOREIGN KEY (tx_id) REFERENCES transactions(tx_id)
);

CREATE TABLE IF NOT EXISTS transaction_file_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    action_type TEXT NOT NULL CHECK (action_type IN ('CREATE', 'OVERWRITE')),
    staging_path TEXT NOT NULL,
    backup_path TEXT,
    original_sha256 TEXT,
    target_sha256 TEXT NOT NULL,
    original_file_id TEXT,
    staging_file_id TEXT,
    installed_file_id TEXT,
    status TEXT NOT NULL CHECK (status IN (
        'PENDING', 'APPLIED', 'RESTORED', 'ALREADY_RESTORED', 'CONFLICT'
    )),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tx_id) REFERENCES transactions(tx_id),
    UNIQUE(tx_id, relative_path)
);

CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status);
CREATE INDEX IF NOT EXISTS idx_file_log_tx_status ON transaction_file_log(tx_id, status);
"""

CATALOG_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS dlc_items (
    dlc_code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    uncompressed_size_bytes INTEGER NOT NULL,
    sha256 TEXT,
    md5 TEXT,
    download_url TEXT,
    is_installed INTEGER NOT NULL DEFAULT 0,
    last_scanned_at DATETIME
);

CREATE TABLE IF NOT EXISTS download_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dlc_code TEXT NOT NULL,
    downloaded_bytes INTEGER NOT NULL,
    total_bytes INTEGER NOT NULL,
    status TEXT NOT NULL,
    completed_at DATETIME
);

CREATE INDEX IF NOT EXISTS idx_dlc_category ON dlc_items(category);
CREATE INDEX IF NOT EXISTS idx_dlc_is_installed ON dlc_items(is_installed);
CREATE INDEX IF NOT EXISTS idx_download_history_dlc ON download_history(dlc_code);
"""

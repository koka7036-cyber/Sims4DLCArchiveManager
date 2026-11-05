"""
Database package for Sims 4 DLC Archive & Install Manager.
Exposes StateDatabase, CatalogDatabase, CrashRecoveryEngine, models, and enums.
Conforms strictly to PROJECT_SPEC.md v1.1.5.
"""

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
    STATE_DB_SCHEMA,
    CATALOG_DB_SCHEMA,
)

from database.database import (
    StateDatabase,
    CatalogDatabase,
)

from database.recovery import (
    CrashRecoveryEngine,
    inspect_file,
    get_file_identity,
    compute_sha256,
)

__all__ = [
    "TransactionStatus",
    "LeaseStatus",
    "FileActionType",
    "FileLogStatus",
    "InspectionResult",
    "TransactionRecord",
    "TransactionLeaseRecord",
    "TransactionFileLogRecord",
    "DLCItemRecord",
    "DownloadHistoryRecord",
    "STATE_DB_SCHEMA",
    "CATALOG_DB_SCHEMA",
    "StateDatabase",
    "CatalogDatabase",
    "CrashRecoveryEngine",
    "inspect_file",
    "get_file_identity",
    "compute_sha256",
]

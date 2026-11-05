"""
Typed exception hierarchy for Sims 4 DLC Archive & Install Manager.
Conforms strictly to PROJECT_SPEC.md v1.1.5.
"""

class Sims4ManagerError(Exception):
    """Base exception for all Sims 4 DLC Archive Manager errors."""
    pass


# ==============================================================================
# Security & Process Coordination Exceptions
# ==============================================================================

class SecurityError(Sims4ManagerError):
    """Base class for security, cryptography, and elevation errors."""
    pass


class DPAPIError(SecurityError):
    """Raised when Windows DPAPI encryption or decryption fails."""
    pass


class InvalidSignatureError(SecurityError):
    """Raised when an Ed25519 digital signature fails verification."""
    pass


class ManifestVerificationError(SecurityError):
    """Raised when release manifest or file checksum verification fails."""
    pass


class WhiteLabelLeakError(SecurityError):
    """Raised when cross-store data or owner tools leak into a distribution package."""
    pass


class KeyVaultError(SecurityError):
    """Raised when Key Vault container export/import or challenge fails."""
    pass


class ChallengeError(SecurityError):
    """Raised when challenge token validation fails in Release Guard."""
    pass


class UACRejectedError(SecurityError):
    """Raised when the user rejects elevation (ERROR_CANCELLED)."""
    pass


class LockContextError(SecurityError):
    """Base class for LockContext lifecycle and mutual exclusion errors."""
    pass


class LockLostException(LockContextError):
    """Raised when a lock handle or lock context is invalidated, inactive, or lost."""
    pass


class LeaseExpiredError(LockContextError):
    """Raised when a transaction lease token is expired, suspended, or invalid."""
    pass


class SharingViolationError(LockContextError):
    """Raised when a file or lock cannot be opened due to ERROR_SHARING_VIOLATION."""
    pass


# ==============================================================================
# Filesystem & Volume Exceptions
# ==============================================================================

class FilesystemError(Sims4ManagerError):
    """Base class for filesystem and volume identification errors."""
    pass


class UnsupportedFilesystemError(FilesystemError):
    """
    Raised when the target volume filesystem is not NTFS (e.g., FAT32, exFAT, ReFS).
    Mandated by PROJECT_SPEC.md section 5.3: operations must be blocked before
    database transaction creation and before disk modifications.
    """
    pass


class ReparsePointError(FilesystemError):
    """Raised when an unauthorized Reparse Point (Symlink, Junction) is detected."""
    pass


class VolumeMismatchError(FilesystemError):
    """Raised when VolumeSerialNumber does not match the target game volume."""
    pass


# ==============================================================================
# Integrity & Verification Exceptions
# ==============================================================================

class IntegrityError(Sims4ManagerError):
    """Base class for data integrity, file id, and cryptographic hash errors."""
    pass


class HashMismatchError(IntegrityError):
    """Raised when SHA-256 or MD5 hash does not match expected value."""
    pass


class FileIdMismatchError(IntegrityError):
    """Raised when 128-bit NTFS FileId does not match expected value."""
    pass


# ==============================================================================
# Transaction & Database Exceptions
# ==============================================================================

class DatabaseError(Sims4ManagerError):
    """Base class for database connection, query, and transaction errors."""
    pass


class DatabaseCorruptionError(DatabaseError):
    """Raised when SQLite database corruption (SQLITE_CORRUPT or failed integrity/quick_check) is detected."""
    pass


class TransactionError(Sims4ManagerError):
    """Base class for FSM state machine and transaction execution errors."""
    pass


class RecoveryConflictError(TransactionError):
    """
    Raised during Crash Recovery when disk state contradicts the transaction log,
    triggering the fail-closed rule and RECOVERY_CONFLICT state.
    """
    pass


class DatabaseCommitError(TransactionError, DatabaseError):
    """Raised when an SQLite COMMIT fails or returns an ambiguous state."""
    pass


# ==============================================================================
# Archive Engine Exceptions
# ==============================================================================

class ArchiveError(Sims4ManagerError):
    """Base class for archive inspection, decompression, and verification errors."""
    pass


class PathTraversalError(ArchiveError):
    """Raised when an archive member attempts relative or absolute path traversal."""
    pass


class BlockedBinaryError(ArchiveError):
    """Raised when an untrusted archive contains prohibited executable files."""
    pass


class UnsupportedArchiveFormatError(ArchiveError):
    """Raised when the archive file format is not recognized or supported."""
    pass


class CorruptedArchiveError(ArchiveError):
    """Raised when an archive file is physically corrupted or fails CRC verification."""
    pass


class ArchiveSecurityLimitError(ArchiveError):
    """Raised when an archive exceeds file count, size, or compression ratio limits."""
    pass


class ArchiveSymlinkError(ArchiveError):
    """Raised when an archive contains prohibited symbolic links or reparse entries."""
    pass


# ==============================================================================
# Network & Yandex.Disk API Exceptions
# ==============================================================================

class NetworkError(Sims4ManagerError):
    """Base class for network communication, connection, and timeout errors."""
    pass


class YandexAPIError(NetworkError):
    """Raised when Yandex.Disk API returns an error response."""
    def __init__(self, message: str, status_code: int = None, error_code: str = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class YandexResourceNotFoundError(YandexAPIError):
    """Raised when requested resource does not exist on Yandex.Disk (HTTP 404)."""
    pass


class YandexRateLimitError(YandexAPIError):
    """Raised when Yandex.Disk rate limit is exceeded (HTTP 429)."""
    pass


class YandexAuthError(YandexAPIError):
    """Raised when Yandex.Disk authorization fails (HTTP 401/403)."""
    pass


# ==============================================================================
# Downloader Exceptions
# ==============================================================================

class DownloadError(NetworkError):
    """Base class for file download errors."""
    pass


class DownloadCancelledError(DownloadError):
    """Raised when a download operation is cancelled by the user."""
    pass


class DownloadSizeMismatchError(DownloadError):
    """Raised when downloaded file size differs from expected size."""
    pass


class DownloadRangeError(DownloadError):
    """Raised when server returns an invalid or incompatible Content-Range."""
    pass

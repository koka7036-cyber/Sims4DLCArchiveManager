"""
Security, DPAPI, lease tokens, and LockContext mutual exclusion.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 6.1, 7.4, 7.6.
"""

import os
import sys
import uuid
import secrets
import hashlib
import threading
from contextlib import contextmanager
from typing import Optional, Tuple, Dict

from core.exceptions import (
    DPAPIError,
    LockContextError,
    LockLostException,
    LeaseExpiredError,
    SharingViolationError,
    SecurityError,
)
from core.logger import get_logger

logger = get_logger("security")

# Platform flag
IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    crypt32 = ctypes.windll.crypt32
    shell32 = ctypes.windll.shell32

    # DATA_BLOB for DPAPI
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    # FILE_ID_INFO (128-bit FileId + VolumeSerialNumber)
    class FILE_ID_128(ctypes.Structure):
        _fields_ = [("Identifier", ctypes.c_byte * 16)]

    class FILE_ID_INFO(ctypes.Structure):
        _fields_ = [
            ("VolumeSerialNumber", ctypes.c_ulonglong),
            ("FileId", FILE_ID_128),
        ]

    FileIdInfoClass = 18

    # Win32 Constants
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_NONE = 0x00000000
    OPEN_ALWAYS = 4
    FILE_ATTRIBUTE_NORMAL = 0x80
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value  # 18446744073709551615 on x64
    ERROR_SHARING_VIOLATION = 32
    ERROR_ACCESS_DENIED = 5
    CRYPTPROTECT_UI_FORBIDDEN = 0x1


def is_invalid_handle(h: Optional[int]) -> bool:
    """
    Checks if a Win32 or POSIX handle is NULL or INVALID_HANDLE_VALUE (-1 or 0xFFFFFFFFFFFFFFFF).
    Protects against signed/unsigned 64-bit integer conversion issues in ctypes.
    """
    if h is None or h == 0:
        return True
    if h == -1 or h == 0xFFFFFFFFFFFFFFFF or h == 18446744073709551615:
        return True
    return False

    # Configure 64-bit ctypes prototypes to prevent pointer/handle truncation on x64
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL

    kernel32.GetLastError.argtypes = []
    kernel32.GetLastError.restype = wintypes.DWORD

    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    kernel32.GetHandleInformation.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetHandleInformation.restype = wintypes.BOOL

    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE

    kernel32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.GetFileInformationByHandleEx.restype = wintypes.BOOL

    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL

    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DATA_BLOB),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL


# ==============================================================================
# Token & Secret Generation
# ==============================================================================

def generate_lease_token() -> str:
    """
    Generates a unique UUIDv4 transaction lease token.
    Mandated by PROJECT_SPEC.md section 7.4.
    """
    return str(uuid.uuid4())


def generate_resume_secret() -> str:
    """
    Generates a cryptographically strong 32-byte hex secret for UAC handoff.
    Mandated by PROJECT_SPEC.md section 7.4.
    """
    return secrets.token_hex(32)


def hash_resume_secret(secret: str) -> str:
    """
    Returns the SHA-256 hex digest of the resume secret for storage in state.db.
    """
    if not isinstance(secret, str) or not secret:
        raise SecurityError("Invalid resume secret for hashing.")
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


# ==============================================================================
# Windows DPAPI Cryptography
# ==============================================================================

# Simulation store for non-Windows test environments
_SHIM_DPAPI_MAGIC = b"__DPAPI_SHIM_V1__"


def encrypt_dpapi(data: bytes, entropy: Optional[bytes] = None) -> bytes:
    """
    Encrypts data using Windows Data Protection API (DPAPI CryptProtectData).
    On non-Windows platforms (e.g. Linux CI/test containers), uses a deterministic
    secure test shim preserving DPAPI semantics.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise DPAPIError("Data to encrypt must be bytes.")
    if len(data) == 0:
        raise DPAPIError("Cannot encrypt empty data block.")

    if IS_WINDOWS:
        out_blob = DATA_BLOB()
        try:
            in_blob = DATA_BLOB()
            in_blob.cbData = len(data)
            in_blob.pbData = ctypes.cast(ctypes.create_string_buffer(bytes(data)), ctypes.POINTER(ctypes.c_byte))

            entropy_blob = None
            entropy_ptr = None
            if entropy:
                entropy_blob = DATA_BLOB()
                entropy_blob.cbData = len(entropy)
                entropy_blob.pbData = ctypes.cast(ctypes.create_string_buffer(bytes(entropy)), ctypes.POINTER(ctypes.c_byte))
                entropy_ptr = ctypes.byref(entropy_blob)

            res = crypt32.CryptProtectData(
                ctypes.byref(in_blob),
                "Sims4DLCArchiveManager_Secret",
                entropy_ptr,
                None,
                None,
                CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(out_blob),
            )
            if not res:
                err = kernel32.GetLastError()
                raise DPAPIError(f"CryptProtectData failed with error code: {err}")

            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        except Exception as e:
            if isinstance(e, DPAPIError):
                raise
            raise DPAPIError(f"DPAPI encryption unexpected error: {e}") from e
        finally:
            if out_blob.pbData:
                kernel32.LocalFree(out_blob.pbData)
    else:
        # Cross-platform test shim for non-Windows test runs
        salt = os.urandom(16)
        key = hashlib.sha256(salt + (entropy or b"Sims4DefaultEntropy")).digest()
        # Simple XOR mask + HMAC tag for test integrity verification
        cipher = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
        tag = hashlib.sha256(key + cipher).digest()[:16]
        return _SHIM_DPAPI_MAGIC + salt + tag + cipher


def is_native_dpapi_available() -> bool:
    """Returns True if running on native Windows with real crypt32.dll DPAPI support."""
    return IS_WINDOWS


def decrypt_dpapi(cipher_data: bytes, entropy: Optional[bytes] = None) -> bytes:
    """
    Decrypts DPAPI-encrypted data using Windows CryptUnprotectData.
    Raises DPAPIError if data is corrupt, empty, or fails authentication.
    """
    if not isinstance(cipher_data, (bytes, bytearray)):
        raise DPAPIError("Cipher data must be bytes.")
    if len(cipher_data) < 16:
        raise DPAPIError("Cipher data too short or corrupt.")

    if IS_WINDOWS:
        out_blob = DATA_BLOB()
        try:
            in_blob = DATA_BLOB()
            in_blob.cbData = len(cipher_data)
            in_blob.pbData = ctypes.cast(ctypes.create_string_buffer(bytes(cipher_data)), ctypes.POINTER(ctypes.c_byte))

            entropy_blob = None
            entropy_ptr = None
            if entropy:
                entropy_blob = DATA_BLOB()
                entropy_blob.cbData = len(entropy)
                entropy_blob.pbData = ctypes.cast(ctypes.create_string_buffer(bytes(entropy)), ctypes.POINTER(ctypes.c_byte))
                entropy_ptr = ctypes.byref(entropy_blob)

            res = crypt32.CryptUnprotectData(
                ctypes.byref(in_blob),
                None,
                entropy_ptr,
                None,
                None,
                CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(out_blob),
            )
            if not res:
                err = kernel32.GetLastError()
                raise DPAPIError(f"CryptUnprotectData failed with error code: {err}")

            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        except Exception as e:
            if isinstance(e, DPAPIError):
                raise
            raise DPAPIError(f"DPAPI decryption failure: {e}") from e
        finally:
            if out_blob.pbData:
                kernel32.LocalFree(out_blob.pbData)
    else:
        # Cross-platform test shim decryption
        if not cipher_data.startswith(_SHIM_DPAPI_MAGIC):
            raise DPAPIError("Data is corrupt or was not encrypted with DPAPI shim.")
        offset = len(_SHIM_DPAPI_MAGIC)
        if len(cipher_data) < offset + 32:
            raise DPAPIError("Cipher block corrupted: truncated payload.")

        salt = cipher_data[offset:offset + 16]
        tag = cipher_data[offset + 16:offset + 32]
        cipher = cipher_data[offset + 32:]

        key = hashlib.sha256(salt + (entropy or b"Sims4DefaultEntropy")).digest()
        expected_tag = hashlib.sha256(key + cipher).digest()[:16]
        if expected_tag != tag:
            raise DPAPIError("Cipher data authentication failed: corrupted or tampered block.")

        return bytes(b ^ key[i % len(key)] for i, b in enumerate(cipher))


# Standard DPAPI API aliases conforming to Windows CryptProtectData naming
dpapi_protect = encrypt_dpapi
dpapi_unprotect = decrypt_dpapi


# ==============================================================================
# LockContext & Exclusive File Locking
# ==============================================================================

# Cross-platform simulated active lock table for non-Windows tests / processes
_GLOBAL_TEST_LOCKS: Dict[str, Tuple[int, int, int]] = {}
_GLOBAL_TEST_LOCKS_MUTEX = threading.Lock()


class LockContext:
    """
    Mandatory transaction lock context for all modifying file operations.
    Conforms to PROJECT_SPEC.md section 7.6.

    Attributes:
        tx_id: Transaction ID (GUID/string)
        lease_token: Active lease token (UUIDv4)
        lock_handle: Low-level file handle/descriptor
        expected_lock_file_id: 128-bit MFT FileId of tx_<tx_id>.lock
        expected_lock_volume_serial: Volume serial number of lock file
        lock_sync: In-process reentrant lock for thread coordination
        is_active: Boolean flag indicating if lock is currently valid
    """

    def __init__(
        self,
        tx_id: str,
        lease_token: str,
        lock_handle: int,
        expected_lock_file_id: int,
        expected_lock_volume_serial: int,
        lock_path: Optional[str] = None
    ):
        self.tx_id = tx_id
        self.lease_token = lease_token
        self.lock_handle = lock_handle
        self.expected_lock_file_id = expected_lock_file_id
        self.expected_lock_volume_serial = expected_lock_volume_serial
        self.lock_sync = threading.RLock()
        self.is_active = True
        self._lock_path = lock_path

    @contextmanager
    def guard(self):
        """
        Guards modifying file operations by enforcing continuous ownership of lock_sync
        and validating handle and lease state before execution.
        Mandated by PROJECT_SPEC.md section 7.6:
        - Step 1: Thread acquires lock_sync
        - Step 2: Verifies is_active == True
        - Step 3: Verifies descriptor validity
        - Step 4: Verifies VolumeSerialNumber and FileId
        - Step 5: Verifies lease_token validity
        - Step 6: Keeps lock_sync held continuously during call
        - Step 7: Releases lock_sync upon completion
        """
        acquired = self.lock_sync.acquire(blocking=True)
        try:
            if not self.is_active:
                raise LockLostException(f"LockContext for tx '{self.tx_id}' is inactive or closed.")

            if is_invalid_handle(self.lock_handle):
                raise LockLostException("Lock handle is invalid or closed.")

            if not self.lease_token or self.lease_token == "EXPIRED":
                raise LeaseExpiredError(f"Lease token is invalid or expired for tx '{self.tx_id}'.")

            # Platform handle & identity verification
            if IS_WINDOWS:
                flags = wintypes.DWORD()
                if not kernel32.GetHandleInformation(self.lock_handle, ctypes.byref(flags)):
                    raise LockLostException("GetHandleInformation failed: handle is invalid or closed.")

                file_info = FILE_ID_INFO()
                res = kernel32.GetFileInformationByHandleEx(
                    self.lock_handle,
                    FileIdInfoClass,
                    ctypes.byref(file_info),
                    ctypes.sizeof(file_info)
                )
                if not res:
                    raise LockLostException("Failed to query FileIdInfo on lock handle.")

                # Convert 128-bit Identifier bytes to int
                current_file_id = int.from_bytes(bytes(file_info.FileId.Identifier), byteorder="little")
                if file_info.VolumeSerialNumber != self.expected_lock_volume_serial:
                    raise LockLostException("Lock volume serial number mismatch.")
                if current_file_id != self.expected_lock_file_id:
                    raise LockLostException("Lock 128-bit FileId mismatch.")
            else:
                # Non-windows verification
                if self._lock_path:
                    norm_path = os.path.abspath(self._lock_path)
                    with _GLOBAL_TEST_LOCKS_MUTEX:
                        if norm_path not in _GLOBAL_TEST_LOCKS:
                            raise LockLostException("Lock file not found in active lock registry.")
                        reg_handle, reg_vol, reg_file_id = _GLOBAL_TEST_LOCKS[norm_path]
                        if reg_handle != self.lock_handle:
                            raise LockLostException("Lock descriptor mismatch.")
                        if reg_vol != self.expected_lock_volume_serial or reg_file_id != self.expected_lock_file_id:
                            raise LockLostException("Lock identity mismatch in registry.")

            yield self

        finally:
            self.lock_sync.release()

    def close(self):
        """
        Closes the lock handle under strict lock_sync protection to prevent
        race conditions with threads executing inside guard().
        Mandated by PROJECT_SPEC.md section 7.6.
        """
        with self.lock_sync:
            if not self.is_active:
                return
            self.is_active = False

            if IS_WINDOWS:
                if self.lock_handle and not is_invalid_handle(self.lock_handle):
                    kernel32.CloseHandle(self.lock_handle)
            else:
                if self._lock_path:
                    norm_path = os.path.abspath(self._lock_path)
                    with _GLOBAL_TEST_LOCKS_MUTEX:
                        _GLOBAL_TEST_LOCKS.pop(norm_path, None)
                if self.lock_handle and self.lock_handle > 0:
                    try:
                        os.close(self.lock_handle)
                    except OSError:
                        pass

            self.lock_handle = 0


def acquire_lock_file(lock_file_path: str, tx_id: str, lease_token: str) -> LockContext:
    """
    Acquires an exclusive lock file (tx_<tx_id>.lock) with dwShareMode = 0.
    Raises SharingViolationError if already locked by another process/handle.
    Mandated by PROJECT_SPEC.md section 7.6.
    """
    os.makedirs(os.path.dirname(os.path.abspath(lock_file_path)), exist_ok=True)
    norm_path = os.path.abspath(lock_file_path)

    if IS_WINDOWS:
        handle = kernel32.CreateFileW(
            ctypes.c_wchar_p(norm_path),
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_NONE,  # dwShareMode = 0 (exclusive)
            None,
            OPEN_ALWAYS,
            FILE_ATTRIBUTE_NORMAL,
            None
        )
        if is_invalid_handle(handle):
            err = kernel32.GetLastError()
            if err == ERROR_SHARING_VIOLATION or err == ERROR_ACCESS_DENIED:
                raise SharingViolationError(f"Exclusive lock violation on '{norm_path}' (Win32 error: {err}).")
            raise LockContextError(f"Failed to create/open lock file: {err}")

        # Retrieve FileIdInfo
        file_info = FILE_ID_INFO()
        res = kernel32.GetFileInformationByHandleEx(
            handle,
            FileIdInfoClass,
            ctypes.byref(file_info),
            ctypes.sizeof(file_info)
        )
        if not res:
            err = kernel32.GetLastError()
            kernel32.CloseHandle(handle)
            raise LockContextError(f"Failed to retrieve lock FileIdInfo: {err}")

        vol_serial = int(file_info.VolumeSerialNumber)
        file_id = int.from_bytes(bytes(file_info.FileId.Identifier), byteorder="little")

        return LockContext(
            tx_id=tx_id,
            lease_token=lease_token,
            lock_handle=handle,
            expected_lock_file_id=file_id,
            expected_lock_volume_serial=vol_serial,
            lock_path=norm_path
        )
    else:
        # Cross-platform / POSIX unit test emulation
        with _GLOBAL_TEST_LOCKS_MUTEX:
            if norm_path in _GLOBAL_TEST_LOCKS:
                raise SharingViolationError(f"Exclusive lock violation on '{norm_path}' (already held).")

            fd = os.open(norm_path, os.O_RDWR | os.O_CREAT, 0o600)
            # Compute deterministic simulated Volume and FileId from inode / st_dev
            stat_res = os.fstat(fd)
            vol_serial = int(stat_res.st_dev)
            file_id = int(stat_res.st_ino)

            _GLOBAL_TEST_LOCKS[norm_path] = (fd, vol_serial, file_id)

            return LockContext(
                tx_id=tx_id,
                lease_token=lease_token,
                lock_handle=fd,
                expected_lock_file_id=file_id,
                expected_lock_volume_serial=vol_serial,
                lock_path=norm_path
            )


# ==============================================================================
# Permissions & Elevation Checks
# ==============================================================================

def is_admin() -> bool:
    """
    Returns True if the current process is running with elevated (Administrator/root) privileges.
    """
    if IS_WINDOWS:
        try:
            return bool(shell32.IsUserAnAdmin())
        except Exception:
            return False
    else:
        return os.geteuid() == 0


def check_write_permission(directory_path: str) -> bool:
    """
    Verifies actual write permission in the target directory by creating
    and removing a temporary test file.
    """
    if not os.path.exists(directory_path):
        return False

    test_file = os.path.join(directory_path, f".test_perm_{uuid.uuid4().hex}.tmp")
    try:
        with open(test_file, "wb") as f:
            f.write(b"ok")
        os.remove(test_file)
        return True
    except (PermissionError, OSError):
        return False

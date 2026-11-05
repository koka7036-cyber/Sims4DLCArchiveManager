"""
Low-level safe Windows NT handle wrapper and directory step traversal.
Strictly conforms to PROJECT_SPEC.md v1.1.5 sections 5.3, 7.1, 7.2, 7.3, 7.6.
"""

from __future__ import annotations

import os
import sys
import ctypes
from pathlib import Path
from typing import Optional, Tuple, List, Union

from core.exceptions import (
    FilesystemError,
    ReparsePointError,
    VolumeMismatchError,
    SharingViolationError,
    LockContextError,
)
from core.logger import get_logger
from core.security import is_invalid_handle, LockContext

logger = get_logger("core.nt_handle")

IS_WINDOWS = sys.platform == "win32"

# NT / Win32 Constants
STATUS_SUCCESS = 0x00000000
STATUS_OBJECT_NAME_NOT_FOUND = 0xC0000034
STATUS_OBJECT_NAME_COLLISION = 0xC0000035
STATUS_ACCESS_DENIED = 0xC0000022
STATUS_REPARSE_POINT_ENCOUNTERED = 0xC0000275

FILE_DIRECTORY_FILE = 0x00000001
FILE_NON_DIRECTORY_FILE = 0x00000040
FILE_SYNCHRONOUS_IO_NONALERT = 0x00000020
FILE_OPEN_FOR_BACKUP_INTENT = 0x00004000
FILE_OPEN_REPARSE_POINT = 0x00200000

FILE_OPEN = 0x00000001
FILE_CREATE = 0x00000002
FILE_OPEN_IF = 0x00000003

FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004

OBJ_CASE_INSENSITIVE = 0x00000040

FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
FILE_ATTRIBUTE_NORMAL = 0x00000080

# Desired access rights per section 7.2.2
ACCESS_TARGET_DIR = 0x00100083       # FILE_ADD_FILE | FILE_LIST_DIRECTORY | FILE_TRAVERSE | SYNCHRONIZE
ACCESS_NAVIGATION = 0x001000A1       # FILE_LIST_DIRECTORY | FILE_TRAVERSE | SYNCHRONIZE
ACCESS_ORIGINAL_FILE = 0x00110081    # DELETE | FILE_READ_DATA | FILE_READ_ATTRIBUTES | SYNCHRONIZE

FileRenameInfoClass = 3
FileIdInfoClass = 18

if IS_WINDOWS:
    from ctypes import wintypes
    kernel32 = ctypes.windll.kernel32
    ntdll = ctypes.windll.ntdll

    class UNICODE_STRING(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class OBJECT_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(UNICODE_STRING)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        ]

    class IO_STATUS_BLOCK(ctypes.Structure):
        _fields_ = [
            ("Status", ctypes.c_ulong),
            ("Information", ctypes.c_ulonglong),
        ]

    class FILE_ID_128(ctypes.Structure):
        _fields_ = [("Identifier", ctypes.c_byte * 16)]

    class FILE_ID_INFO(ctypes.Structure):
        _fields_ = [
            ("VolumeSerialNumber", ctypes.c_ulonglong),
            ("FileId", FILE_ID_128),
        ]

    class FILE_RENAME_INFO(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BOOLEAN),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.ULONG),
            ("FileName", wintypes.WCHAR * 1024),
        ]

    # Setup NtCreateFile prototype
    ntdll.NtCreateFile.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        ctypes.POINTER(OBJECT_ATTRIBUTES),
        ctypes.POINTER(IO_STATUS_BLOCK),
        ctypes.POINTER(ctypes.c_longlong),
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
    ]
    ntdll.NtCreateFile.restype = ctypes.c_ulong

    ntdll.NtClose.argtypes = [wintypes.HANDLE]
    ntdll.NtClose.restype = ctypes.c_ulong


class SafeNTHandle:
    """
    Manages the lifecycle of an NT / Win32 or POSIX file/directory handle.
    Guarantees safe closure and validation.
    """

    def __init__(self, handle: int, path: str, is_dir: bool = False, volume_serial: Optional[int] = None):
        self.handle = handle
        self.path = os.path.abspath(path)
        self.is_dir = is_dir
        self.volume_serial = volume_serial
        self._is_closed = False

    def is_valid(self) -> bool:
        """Returns True if the handle is non-zero, not closed, and not INVALID_HANDLE_VALUE."""
        if self._is_closed:
            return False
        return not is_invalid_handle(self.handle)

    def close(self) -> None:
        """Closes the underlying low-level handle."""
        if self._is_closed:
            return
        self._is_closed = True

        if IS_WINDOWS:
            if not is_invalid_handle(self.handle):
                ntdll.NtClose(self.handle)
        else:
            if self.handle > 0:
                try:
                    os.close(self.handle)
                except OSError:
                    pass
        self.handle = 0

    def __enter__(self) -> "SafeNTHandle":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def open_directory_handle(
    dir_path: str,
    desired_access: int = ACCESS_NAVIGATION,
    create_if_missing: bool = False
) -> SafeNTHandle:
    """
    Opens an NT handle to a directory with FILE_OPEN_REPARSE_POINT.
    Rejects Reparse Points (Symlinks, Junctions) per Section 7.2.
    """
    norm_path = os.path.abspath(dir_path)

    if IS_WINDOWS:
        if create_if_missing:
            os.makedirs(norm_path, exist_ok=True)

        nt_path = f"\\??\\{norm_path}"
        us = UNICODE_STRING(len(nt_path) * 2, (len(nt_path) + 1) * 2, nt_path)
        oa = OBJECT_ATTRIBUTES(
            Length=ctypes.sizeof(OBJECT_ATTRIBUTES),
            RootDirectory=0,
            ObjectName=ctypes.byref(us),
            Attributes=OBJ_CASE_INSENSITIVE,
            SecurityDescriptor=None,
            SecurityQualityOfService=None,
        )
        iosb = IO_STATUS_BLOCK()
        h = wintypes.HANDLE()

        disposition = FILE_OPEN_IF if create_if_missing else FILE_OPEN
        options = FILE_DIRECTORY_FILE | FILE_SYNCHRONOUS_IO_NONALERT | FILE_OPEN_FOR_BACKUP_INTENT | FILE_OPEN_REPARSE_POINT

        status = ntdll.NtCreateFile(
            ctypes.byref(h),
            desired_access,
            ctypes.byref(oa),
            ctypes.byref(iosb),
            None,
            0,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            disposition,
            options,
            None,
            0,
        )

        if status != STATUS_SUCCESS:
            if status == STATUS_ACCESS_DENIED:
                raise PermissionError(f"Access denied opening directory '{norm_path}' (NTSTATUS: 0x{status:08X})")
            if status == STATUS_OBJECT_NAME_NOT_FOUND:
                raise FileNotFoundError(f"Directory not found: '{norm_path}'")
            raise FilesystemError(f"NtCreateFile failed on '{norm_path}' (NTSTATUS: 0x{status:08X})")

        # Check for Reparse Point
        info = FILE_ID_INFO()
        kernel32.GetFileInformationByHandleEx(h, FileIdInfoClass, ctypes.byref(info), ctypes.sizeof(info))
        vol_serial = int(info.VolumeSerialNumber)

        # Attribute check
        attrs = kernel32.GetFileAttributesW(ctypes.c_wchar_p(norm_path))
        if attrs != 0xFFFFFFFF and (attrs & FILE_ATTRIBUTE_REPARSE_POINT):
            ntdll.NtClose(h)
            raise ReparsePointError(f"Reparse Point detected at '{norm_path}'. Symbolic links and junctions are prohibited.")

        return SafeNTHandle(handle=h.value, path=norm_path, is_dir=True, volume_serial=vol_serial)
    else:
        # Cross-platform / POSIX emulation
        if create_if_missing:
            os.makedirs(norm_path, exist_ok=True)

        if not os.path.exists(norm_path):
            raise FileNotFoundError(f"Directory not found: '{norm_path}'")

        if os.path.islink(norm_path):
            raise ReparsePointError(f"Reparse Point detected at '{norm_path}'. Symbolic links are prohibited.")

        # O_NOFOLLOW prevents following symlinks
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW

        fd = os.open(norm_path, flags)
        st = os.fstat(fd)
        return SafeNTHandle(handle=fd, path=norm_path, is_dir=True, volume_serial=int(st.st_dev))


def step_traverse_directories(
    base_root: str,
    relative_path: str,
    desired_access: int = ACCESS_TARGET_DIR,
    create_missing: bool = True,
    expected_volume_serial: Optional[int] = None
) -> SafeNTHandle:
    """
    Step-by-step traversal from base_root to the target directory containing relative_path.
    Opens handles sequentially, rejecting Reparse Points and verifying VolumeSerialNumber
    at each step per Section 7.2.
    """
    base_root = os.path.abspath(base_root)
    # Extract parent directory relative to base_root
    parent_rel = os.path.dirname(relative_path.replace("\\", "/"))
    if not parent_rel or parent_rel == ".":
        target_dir = base_root
        parts = []
    else:
        target_dir = os.path.join(base_root, parent_rel)
        parts = Path(parent_rel).parts

    # 1. Open base root handle
    current_handle = open_directory_handle(base_root, ACCESS_NAVIGATION, create_if_missing=False)

    if expected_volume_serial is not None and current_handle.volume_serial != expected_volume_serial:
        current_handle.close()
        raise VolumeMismatchError(
            f"Volume serial mismatch at root '{base_root}': expected {expected_volume_serial}, got {current_handle.volume_serial}"
        )

    current_path = base_root

    # 2. Sequential traversal step-by-step
    for part in parts:
        next_path = os.path.join(current_path, part)
        try:
            next_handle = open_directory_handle(next_path, ACCESS_NAVIGATION, create_if_missing=create_missing)
        except Exception:
            current_handle.close()
            raise

        # Check VolumeSerialNumber continuity
        if current_handle.volume_serial is not None and next_handle.volume_serial != current_handle.volume_serial:
            next_handle.close()
            current_handle.close()
            raise VolumeMismatchError(f"Volume serial mismatch at step '{next_path}'")

        # Close previous handle immediately
        current_handle.close()
        current_handle = next_handle
        current_path = next_path

    # 3. Final parent directory handle with desired access
    if desired_access != ACCESS_NAVIGATION:
        final_handle = open_directory_handle(target_dir, desired_access, create_if_missing=create_missing)
        current_handle.close()
        return final_handle

    return current_handle


def move_file_replace_if_exists_false(
    source_path: str,
    target_path: str,
    lock_context: Optional[LockContext] = None
) -> None:
    """
    Performs atomic file relocation strictly with ReplaceIfExists = FALSE semantics.
    If target_path already exists, raises FileExistsError without overwriting it.
    Mandated by PROJECT_SPEC.md sections 7.3.1 and 7.3.2.
    """
    norm_src = os.path.abspath(source_path)
    norm_dst = os.path.abspath(target_path)

    if not os.path.exists(norm_src):
        raise FileNotFoundError(f"Source file does not exist: '{norm_src}'")

    os.makedirs(os.path.dirname(norm_dst), exist_ok=True)

    if IS_WINDOWS:
        # Win32 SetFileInformationByHandle with FileRenameInfo ReplaceIfExists = FALSE
        h_src = kernel32.CreateFileW(
            ctypes.c_wchar_p(norm_src),
            ACCESS_ORIGINAL_FILE,
            FILE_SHARE_READ,
            None,
            3,  # OPEN_EXISTING
            FILE_ATTRIBUTE_NORMAL,
            None
        )
        if is_invalid_handle(h_src):
            err = kernel32.GetLastError()
            if err == 32:  # ERROR_SHARING_VIOLATION
                raise SharingViolationError(f"Sharing violation opening source '{norm_src}'")
            raise OSError(f"Failed to open source file handle: Win32 error {err}")

        try:
            # Prepare rename info
            fri = FILE_RENAME_INFO()
            fri.ReplaceIfExists = False  # STRICTLY FALSE
            fri.RootDirectory = 0
            fri.FileNameLength = len(norm_dst) * 2
            ctypes.memmove(ctypes.byref(fri, FILE_RENAME_INFO.FileName.offset), norm_dst, len(norm_dst) * 2)

            res = kernel32.SetFileInformationByHandle(
                h_src,
                FileRenameInfoClass,
                ctypes.byref(fri),
                ctypes.sizeof(fri)
            )
            if not res:
                err = kernel32.GetLastError()
                if err == 80 or err == 183:  # ERROR_FILE_EXISTS / ERROR_ALREADY_EXISTS
                    raise FileExistsError(f"Target file already exists: '{norm_dst}' (ReplaceIfExists=FALSE)")
                if err == 32:
                    raise SharingViolationError(f"Sharing violation during rename to '{norm_dst}'")
                raise OSError(f"SetFileInformationByHandle failed: Win32 error {err}")
        finally:
            kernel32.CloseHandle(h_src)
    else:
        # POSIX implementation with atomic ReplaceIfExists = FALSE check
        # Under POSIX, os.rename(src, dst) silently overwrites dst!
        # To guarantee ReplaceIfExists = FALSE without TOCTOU races:
        # We use os.link(src, dst) which atomically fails with FileExistsError if dst exists,
        # followed by os.unlink(src).
        try:
            os.link(norm_src, norm_dst)
            os.unlink(norm_src)
        except FileExistsError:
            raise FileExistsError(f"Target file already exists: '{norm_dst}' (ReplaceIfExists=FALSE)")
        except OSError as e:
            # If across filesystems, raise FilesystemError
            if "cross-device" in str(e).lower() or e.errno == 18:
                raise FilesystemError(f"Cross-volume move is prohibited: {e}")
            raise

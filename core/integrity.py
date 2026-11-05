"""
Physical object identity and cryptographic integrity verification module.
Strictly conforms to PROJECT_SPEC.md v1.1.5 sections 5.3, 7.6, 8.5.
Provides triple-verification (VolumeSerialNumber, FileId, SHA-256) and Hard Link anomaly protection.
"""

from __future__ import annotations

import os
import hashlib
from typing import Optional, Tuple, Dict, Any, List

from core.exceptions import (
    IntegrityError,
    HashMismatchError,
    FileIdMismatchError,
    VolumeMismatchError,
    RecoveryConflictError,
    LockContextError,
)
from core.logger import get_logger
from core.security import LockContext
from database.models import InspectionResult
from database.recovery import get_file_identity, compute_sha256, inspect_file

logger = get_logger("core.integrity")


def verify_triple_identity(
    file_path: str,
    expected_volume_serial: Optional[int] = None,
    expected_file_id: Optional[str] = None,
    expected_sha256: Optional[str] = None,
    lock_context: Optional[LockContext] = None
) -> Tuple[InspectionResult, Optional[int], Optional[int], Optional[str]]:
    """
    Performs full triple-verification (VolumeSerialNumber, FileId, SHA-256) on an existing file.
    If lock_context is provided, validation executes under lock_context.guard().
    """
    if lock_context is not None:
        with lock_context.guard():
            result, vol, fid, sha = inspect_file(file_path, expected_sha256, expected_file_id)
            if result == InspectionResult.VERIFIED_OBJECT and expected_volume_serial is not None:
                if vol != expected_volume_serial:
                    return InspectionResult.OBJECT_IDENTITY_MISMATCH, vol, fid, sha
            return result, vol, fid, sha
    else:
        result, vol, fid, sha = inspect_file(file_path, expected_sha256, expected_file_id)
        if result == InspectionResult.VERIFIED_OBJECT and expected_volume_serial is not None:
            if vol != expected_volume_serial:
                return InspectionResult.OBJECT_IDENTITY_MISMATCH, vol, fid, sha
        return result, vol, fid, sha


def assert_verified_object(
    file_path: str,
    expected_volume_serial: Optional[int] = None,
    expected_file_id: Optional[str] = None,
    expected_sha256: Optional[str] = None,
    lock_context: Optional[LockContext] = None
) -> Tuple[int, int, str]:
    """
    Asserts that the file exists and passes all triple-verification checks.
    Raises specific typed exceptions (HashMismatchError, FileIdMismatchError, VolumeMismatchError, FileNotFoundError) on failure.
    Returns: (volume_serial, file_id, actual_sha256).
    """
    res, vol, fid, sha = verify_triple_identity(
        file_path, expected_volume_serial, expected_file_id, expected_sha256, lock_context
    )
    if res == InspectionResult.OBJECT_NOT_FOUND:
        raise FileNotFoundError(f"Object not found: '{file_path}'")
    if res == InspectionResult.HASH_MISMATCH:
        raise HashMismatchError(f"SHA-256 hash mismatch for '{file_path}': expected {expected_sha256}, got {sha}")
    if res == InspectionResult.OBJECT_IDENTITY_MISMATCH:
        if expected_volume_serial is not None and vol != expected_volume_serial:
            raise VolumeMismatchError(f"Volume serial mismatch for '{file_path}': expected {expected_volume_serial}, got {vol}")
        raise FileIdMismatchError(f"FileId identity mismatch for '{file_path}': expected {expected_file_id}, got {fid}")
    if res == InspectionResult.ACCESS_DENIED:
        raise PermissionError(f"Access denied accessing '{file_path}'")
    if res == InspectionResult.SHARING_VIOLATION:
        raise OSError(f"Sharing violation accessing '{file_path}'")
    if res != InspectionResult.VERIFIED_OBJECT:
        raise IntegrityError(f"Object verification failed for '{file_path}' with status: {res.value}")

    return vol, fid, sha


def detect_hardlink_anomalies(
    target_path: Optional[str],
    backup_path: Optional[str],
    staging_path: Optional[str]
) -> bool:
    """
    Preliminary inspection per Section 8.5:
    Verifies that no two distinct paths share the same (VolumeSerialNumber, FileId).
    Returns True if an anomaly is detected (requiring immediate CONFLICT).
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

    seen: Dict[Tuple[int, int], str] = {}
    for label, path in paths:
        try:
            ident = get_file_identity(path)
            if ident in seen:
                other_label = seen[ident]
                logger.critical(
                    f"Hard Link anomaly detected between {label} and {other_label} "
                    f"(identical Volume & FileId: {ident})"
                )
                return True
            seen[ident] = label
        except Exception as e:
            logger.debug(f"Could not read identity of '{path}': {e}")
            continue

    return False

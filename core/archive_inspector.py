"""
Archive inspector, path traversal guard, and DLC structure normalizer.
Conforms strictly to PROJECT_SPEC.md v1.1.5 Section 5, 10 and Stage 5 requirements.
Operates strictly read-only without modifying or extracting files to disk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Set, Union

from core.archive_manager import ArchiveEntry, ArchiveFormat, ArchiveManager
from core.exceptions import (
    ArchiveError,
    ArchiveSecurityLimitError,
    ArchiveSymlinkError,
    BlockedBinaryError,
    CorruptedArchiveError,
    PathTraversalError,
)
from core.logger import get_logger

logger = get_logger("core.archive_inspector")

# Security Limits (anti-Zip Bomb & resource exhaustion)
DEFAULT_MAX_FILE_COUNT = 50_000
DEFAULT_MAX_UNCOMPRESSED_SIZE = 50 * 1024 * 1024 * 1024  # 50 GB
DEFAULT_MAX_RATIO = 100.0  # Max uncompressed / compressed ratio

# Prohibited executable / script extensions in regular DLC archives per Section 10.3
PROHIBITED_EXTENSIONS: Set[str] = {
    ".exe", ".bat", ".cmd", ".dll", ".vbs", ".ps1", ".scr",
    ".com", ".pif", ".msi", ".reg", ".cpl", ".hta", ".wsf",
    ".vbe", ".jse", ".sys", ".drv"
}

# Windows reserved device names that must never be created
RESERVED_DEVICE_NAMES: Set[str] = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
}

# Regex to detect DLC code folder (e.g., EP01, GP05, SP12, FP01)
DLC_FOLDER_REGEX = re.compile(r"^(EP|GP|SP|FP)(\d{2})$", re.IGNORECASE)
STANDARD_ROOT_FOLDERS: Set[str] = {"DELTA", "__INSTALLER", "DATA", "GAME", "SUPPORT"}


@dataclass
class InspectionReport:
    """Diagnostic report produced by DLCArchiveInspector without disk modification."""
    archive_path: Path
    format: ArchiveFormat
    total_files: int
    total_dirs: int
    total_uncompressed_bytes: int
    total_compressed_bytes: int
    detected_dlc_code: Optional[str]
    has_package_files: bool
    package_count: int
    entries: List[ArchiveEntry]
    safe_relative_paths: Dict[str, str] = field(default_factory=dict)
    integrity_passed: bool = True
    is_valid: bool = True
    error_message: Optional[str] = None


def validate_safe_path(entry_path: str, target_dir: Optional[Union[str, Path]] = None) -> str:
    """
    Validates that an archive path is completely safe against Path Traversal vulnerabilities:
    - Rejects paths containing '..' relative traversal.
    - Rejects absolute Unix paths (starting with '/').
    - Rejects Windows drive paths (e.g. 'C:', 'D:\\', 'c:/').
    - Rejects Windows UNC network paths (e.g. '\\\\server\\share').
    - Rejects embedded null bytes.
    - Rejects Windows reserved device names ('CON', 'NUL', 'PRN', 'AUX', 'COM1-9', 'LPT1-9').
    - Rejects paths resolving outside target_dir if provided.
    Returns a cleaned, normalized relative POSIX path.
    """
    if not isinstance(entry_path, str) or not entry_path.strip():
        raise PathTraversalError("Archive entry path must be a non-empty string")

    # 1. Null byte check
    if "\x00" in entry_path:
        raise PathTraversalError(f"Embedded null byte detected in entry path: {repr(entry_path)}")

    # 2. Normalize backslashes to forward slashes
    normalized = entry_path.replace("\\", "/").strip()

    # 3. Windows drive letter check: e.g. "C:..." or "C:/..."
    if re.match(r"^[a-zA-Z]:", normalized):
        raise PathTraversalError(f"Absolute Windows drive letter in entry path: '{entry_path}'")

    # 4. Windows UNC / double slash check
    if normalized.startswith("//"):
        raise PathTraversalError(f"UNC network path in entry path: '{entry_path}'")

    # 5. Absolute path check
    if normalized.startswith("/"):
        raise PathTraversalError(f"Absolute Unix path in entry path: '{entry_path}'")

    # 6. Component-by-component traversal and reserved name validation
    parts = normalized.split("/")
    cleaned_parts: List[str] = []

    for part in parts:
        if not part or part == ".":
            continue
        if part == "..":
            raise PathTraversalError(f"Parent directory traversal '..' detected in: '{entry_path}'")

        # Check Windows reserved device names
        stem = part.split(".")[0].upper()
        if stem in RESERVED_DEVICE_NAMES:
            raise PathTraversalError(f"Windows reserved device name detected: '{part}' in '{entry_path}'")

        cleaned_parts.append(part)

    if not cleaned_parts:
        return ""

    safe_path = "/".join(cleaned_parts)

    # 7. Target directory containment check
    if target_dir is not None:
        target_root = Path(target_dir).resolve()
        candidate = (target_root / safe_path).resolve()
        try:
            candidate.relative_to(target_root)
        except ValueError:
            raise PathTraversalError(
                f"Path traversal escape: '{entry_path}' resolves to '{candidate}' outside '{target_root}'"
            )

    return safe_path


class DLCArchiveInspector:
    """
    Security inspector and structure analyzer for DLC and helper archives.
    Operates strictly in read-only memory mode without unpacking to disk.
    """

    def __init__(
        self,
        max_file_count: int = DEFAULT_MAX_FILE_COUNT,
        max_uncompressed_size: int = DEFAULT_MAX_UNCOMPRESSED_SIZE,
        max_ratio: float = DEFAULT_MAX_RATIO,
    ) -> None:
        self.max_file_count = max_file_count
        self.max_uncompressed_size = max_uncompressed_size
        self.max_ratio = max_ratio

    def inspect_archive(
        self,
        archive_path: Union[str, Path],
        is_helper_archive: bool = False,
        target_dir: Optional[Union[str, Path]] = None,
    ) -> InspectionReport:
        """
        Performs full security audit and structural inspection of an archive.
        - Verifies archive integrity (CRC / header structure).
        - Audits all members for path traversal.
        - Audits for unauthorized symbolic links.
        - Enforces resource exhaustion and zip-bomb safety limits.
        - Enforces prohibited binary whitelist / blacklist per Section 10.3.
        - Normalizes nested DLC folders to the game root structure.
        """
        p = Path(archive_path)
        archive_format = ArchiveManager.detect_archive_format(p)

        # 1. Test integrity without disk extraction
        ArchiveManager.test_archive_integrity(p)

        # 2. List members
        entries = ArchiveManager.list_entries(p)

        total_files = 0
        total_dirs = 0
        total_uncompressed = 0
        total_compressed = 0
        package_count = 0
        has_package_files = False
        safe_relative_paths: Dict[str, str] = {}

        # 3. Security Limits Check (file count)
        if len(entries) > self.max_file_count:
            raise ArchiveSecurityLimitError(
                f"Archive {p.name} contains {len(entries)} items, exceeding maximum allowed limit ({self.max_file_count})"
            )

        # 4. Audit each member
        for entry in entries:
            # Check for prohibited symbolic links
            if entry.is_symlink:
                raise ArchiveSymlinkError(
                    f"Symbolic link detected in archive: '{entry.path}'. Symlinks are strictly prohibited."
                )

            # Validate path against all forms of Path Traversal
            safe_norm_path = validate_safe_path(entry.path, target_dir=target_dir)

            if entry.is_dir:
                total_dirs += 1
            else:
                total_files += 1
                total_uncompressed += entry.uncompressed_size
                total_compressed += entry.compressed_size

                # Check for prohibited executables in standard DLC archives
                if not is_helper_archive:
                    lower_entry_name = entry.path.lower()
                    for bad_ext in PROHIBITED_EXTENSIONS:
                        if lower_entry_name.endswith(bad_ext):
                            raise BlockedBinaryError(
                                f"Prohibited executable/binary '{entry.path}' detected in DLC archive {p.name}. "
                                f"DLC archives must contain only game resources per Section 10.3."
                            )

                # Check for .package file presence
                if entry.path.lower().endswith(".package"):
                    package_count += 1
                    has_package_files = True

            safe_relative_paths[entry.path] = safe_norm_path

        # 5. Security Limits Check (total uncompressed size and compression ratio)
        if total_uncompressed > self.max_uncompressed_size:
            raise ArchiveSecurityLimitError(
                f"Archive uncompressed size ({total_uncompressed} bytes) exceeds safety limit ({self.max_uncompressed_size} bytes)"
            )

        if total_compressed > 1024 and total_uncompressed > 1024 * 1024:
            ratio = total_uncompressed / max(1, total_compressed)
            if ratio > self.max_ratio:
                raise ArchiveSecurityLimitError(
                    f"Archive compression ratio ({ratio:.1f}:1) exceeds maximum safe limit ({self.max_ratio}:1)"
                )

        # 6. Structural DLC code detection and nesting normalization
        detected_dlc_code = self._detect_and_normalize_dlc_structure(safe_relative_paths)

        return InspectionReport(
            archive_path=p,
            format=archive_format,
            total_files=total_files,
            total_dirs=total_dirs,
            total_uncompressed_bytes=total_uncompressed,
            total_compressed_bytes=total_compressed,
            detected_dlc_code=detected_dlc_code,
            has_package_files=has_package_files,
            package_count=package_count,
            entries=entries,
            safe_relative_paths=safe_relative_paths,
            integrity_passed=True,
            is_valid=True,
        )

    @staticmethod
    def _detect_and_normalize_dlc_structure(paths_map: Dict[str, str]) -> Optional[str]:
        """
        Analyzes directory paths to detect primary DLC code (e.g., 'EP01', 'GP02')
        and aligns nested directory paths to the game root structure.
        """
        detected_code: Optional[str] = None

        # Search for first DLC directory pattern
        for orig, safe in paths_map.items():
            if not safe:
                continue
            parts = safe.split("/")
            for i, part in enumerate(parts):
                m = DLC_FOLDER_REGEX.match(part)
                if m:
                    detected_code = part.upper()
                    # If DLC folder is wrapped in extra outer folders (e.g. "The Sims 4/EP01/Data")
                    # normalize destination path to start from "EP01/Data"
                    if i > 0:
                        aligned = "/".join(parts[i:])
                        paths_map[orig] = aligned
                    break
            if detected_code:
                break

        # If a wrapping prefix was found for the DLC, normalize all other paths with same prefix
        if detected_code:
            for orig, safe in list(paths_map.items()):
                if not safe:
                    continue
                parts = safe.split("/")
                if detected_code in [p.upper() for p in parts]:
                    idx = [p.upper() for p in parts].index(detected_code)
                    if idx > 0:
                        paths_map[orig] = "/".join(parts[idx:])

        return detected_code

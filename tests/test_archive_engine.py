"""
Unit tests for unified archive engine and DLCArchiveInspector.
Conforms strictly to PROJECT_SPEC.md v1.1.5 Section 5, 10 and Stage 5 requirements.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from core.archive_inspector import (
    DEFAULT_MAX_FILE_COUNT,
    DEFAULT_MAX_UNCOMPRESSED_SIZE,
    DLCArchiveInspector,
    validate_safe_path,
)
from core.archive_manager import (
    ArchiveEntry,
    ArchiveFormat,
    ArchiveManager,
    ZipArchiveReader,
    detect_archive_format,
)
from core.exceptions import (
    ArchiveSecurityLimitError,
    ArchiveSymlinkError,
    BlockedBinaryError,
    CorruptedArchiveError,
    PathTraversalError,
    UnsupportedArchiveFormatError,
)
from tests.conftest import IsolatedEnvironmentTestCase


class TestArchiveEngine(IsolatedEnvironmentTestCase):

    def setUp(self):
        super().setUp()
        self.archives_dir = Path(self.test_dir) / "archives"
        self.archives_dir.mkdir(parents=True, exist_ok=True)
        self.inspector = DLCArchiveInspector()

    def _create_zip(self, file_dict: dict[str, bytes], zip_filename: str = "test.zip") -> Path:
        """Helper to create a real ZIP archive in temporary directory."""
        path = self.archives_dir / zip_filename
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for arc_name, data in file_dict.items():
                zf.writestr(arc_name, data)
        return path

    def test_scenario_1_valid_zip_archive(self):
        """Scenario 1: Correct ZIP archive detection and inspection."""
        zip_path = self._create_zip({
            "EP01/Data/Client/Strings_RUS_RU.package": b"PACKAGE_RUSSIAN_DATA",
            "EP01/ClientDeltaBuild0.package": b"DELTA_BUILD_DATA_12345",
        }, "valid_ep01.zip")

        fmt = detect_archive_format(zip_path)
        self.assertEqual(fmt, ArchiveFormat.ZIP)

        report = self.inspector.inspect_archive(zip_path)
        self.assertTrue(report.integrity_passed)
        self.assertTrue(report.is_valid)
        self.assertEqual(report.total_files, 2)
        self.assertEqual(report.detected_dlc_code, "EP01")
        self.assertTrue(report.has_package_files)
        self.assertEqual(report.package_count, 2)
        self.assertEqual(report.total_uncompressed_bytes, len(b"PACKAGE_RUSSIAN_DATA") + len(b"DELTA_BUILD_DATA_12345"))

    def test_scenario_2_corrupted_zip_archive(self):
        """Scenario 2: Corrupted ZIP archive fails integrity test and raises CorruptedArchiveError."""
        # Create a truncated / invalid zip file
        bad_path = self.archives_dir / "corrupted.zip"
        with open(bad_path, "wb") as f:
            f.write(b"PK\x03\x04\x14\x00\x00\x00CORRUPTED_INCOMPLETE_ZIP_PAYLOAD")

        with self.assertRaises(CorruptedArchiveError):
            self.inspector.inspect_archive(bad_path)

    def test_scenario_3_empty_archive(self):
        """Scenario 3: Zero-byte file raises CorruptedArchiveError on inspection."""
        empty_path = self.archives_dir / "empty.zip"
        empty_path.touch()

        with self.assertRaises(CorruptedArchiveError):
            detect_archive_format(empty_path)

        with self.assertRaises(CorruptedArchiveError):
            self.inspector.inspect_archive(empty_path)

    def test_scenario_4_archive_with_safe_regular_paths(self):
        """Scenario 4: Safe DLC layout paths pass traversal validation without error."""
        safe_paths = [
            "EP02/Data/Client/Strings_ENG_US.package",
            "EP02/ThumbnailCache/thumb.package",
            "Delta/EP02/ClientDeltaBuild0.package",
            "__Installer/installerdata.xml",
        ]
        for p in safe_paths:
            cleaned = validate_safe_path(p)
            self.assertEqual(cleaned, p)

    def test_scenario_5_path_traversal_dot_dot_blocked(self):
        """Scenario 5: Parent directory traversal '../' is strictly blocked."""
        traversal_attempts = [
            "../outside.txt",
            "EP01/../../escaped.package",
            "Data/Client/../../../Windows/System32/calc.exe",
            "EP01/Data/../..",
        ]
        for bad_path in traversal_attempts:
            with self.subTest(path=bad_path):
                with self.assertRaises(PathTraversalError):
                    validate_safe_path(bad_path)

        # In real zip archive
        bad_zip = self._create_zip({
            "EP01/../../traversal.package": b"MALICIOUS_DATA",
        }, "traversal.zip")

        with self.assertRaises(PathTraversalError):
            self.inspector.inspect_archive(bad_zip)

    def test_scenario_6_absolute_unix_path_blocked(self):
        """Scenario 6: Absolute Unix path starting with '/' is strictly blocked."""
        abs_paths = [
            "/etc/passwd",
            "/home/user/test.package",
            "/EP01/Data/Client.package",
        ]
        for bad_path in abs_paths:
            with self.subTest(path=bad_path):
                with self.assertRaises(PathTraversalError):
                    validate_safe_path(bad_path)

        bad_zip = self._create_zip({
            "/EP01/Client.package": b"ROOT_ESCAPE",
        }, "abs_unix.zip")
        with self.assertRaises(PathTraversalError):
            self.inspector.inspect_archive(bad_zip)

    def test_scenario_7_windows_drive_path_blocked(self):
        """Scenario 7: Windows drive path 'C:\\...' is strictly blocked."""
        drive_paths = [
            r"C:\Windows\System32\malware.dll",
            "D:/Games/The Sims 4/EP01.package",
            "c:relative_drive_escape.txt",
            r"E:\escape.package",
        ]
        for bad_path in drive_paths:
            with self.subTest(path=bad_path):
                with self.assertRaises(PathTraversalError):
                    validate_safe_path(bad_path)

        bad_zip = self._create_zip({
            r"C:\The Sims 4\EP01.package": b"DRIVE_ESCAPE",
        }, "win_drive.zip")
        with self.assertRaises(PathTraversalError):
            self.inspector.inspect_archive(bad_zip)

    def test_scenario_8_mixed_slashes_normalized(self):
        """Scenario 8: Mixed forward and backslashes are normalized cleanly."""
        mixed_path = r"EP01\Data/Client\Strings_RUS_RU.package"
        cleaned = validate_safe_path(mixed_path)
        self.assertEqual(cleaned, "EP01/Data/Client/Strings_RUS_RU.package")

        zip_with_mixed = self._create_zip({
            mixed_path: b"MIXED_CONTENT",
        }, "mixed.zip")
        report = self.inspector.inspect_archive(zip_with_mixed)
        self.assertEqual(report.safe_relative_paths[mixed_path], "EP01/Data/Client/Strings_RUS_RU.package")

    def test_scenario_9_nested_directory_escape_blocked(self):
        """Scenario 9: Nested directory escape resolving outside target_dir is blocked."""
        target_dir = Path(self.test_dir) / "game_root"
        target_dir.mkdir(parents=True, exist_ok=True)

        with self.assertRaises(PathTraversalError):
            validate_safe_path("subdir/../../escape.txt", target_dir=target_dir)

    def test_scenario_10_symlinks_in_archive_strictly_rejected(self):
        """Scenario 10: Symbolic link entries inside archive raise ArchiveSymlinkError."""
        # Create a ZIP containing a Unix symlink entry (file_mode & 0o170000 == 0o120000)
        zip_path = self.archives_dir / "symlink_test.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            info = zipfile.ZipInfo("symlink_pointer")
            # Unix symlink attribute
            info.external_attr = 0o120777 << 16
            zf.writestr(info, "/etc/passwd")

        with self.assertRaises(ArchiveSymlinkError):
            self.inspector.inspect_archive(zip_path)

    def test_scenario_11_max_file_count_limit_exceeded(self):
        """Scenario 11: Archives exceeding max_file_count raise ArchiveSecurityLimitError."""
        strict_inspector = DLCArchiveInspector(max_file_count=3)
        zip_path = self._create_zip({
            "file1.txt": b"1",
            "file2.txt": b"2",
            "file3.txt": b"3",
            "file4.txt": b"4",  # 4th item exceeds limit of 3
        }, "too_many_files.zip")

        with self.assertRaises(ArchiveSecurityLimitError):
            strict_inspector.inspect_archive(zip_path)

    def test_scenario_12_max_uncompressed_size_limit_exceeded(self):
        """Scenario 12: Archives exceeding max_uncompressed_size raise ArchiveSecurityLimitError."""
        strict_inspector = DLCArchiveInspector(max_uncompressed_size=100)
        zip_path = self._create_zip({
            "EP01/large_package.package": b"A" * 150,  # 150 bytes exceeds 100 byte limit
        }, "too_large.zip")

        with self.assertRaises(ArchiveSecurityLimitError):
            strict_inspector.inspect_archive(zip_path)

    def test_scenario_13_inspection_does_not_extract_files_to_disk(self):
        """
        Scenario 13: Integrity test and inspection must NOT extract or write
        any files to the target directory or filesystem.
        """
        target_dir = Path(self.test_dir) / "isolated_game"
        target_dir.mkdir(parents=True, exist_ok=True)
        before_entries = os.listdir(target_dir)

        zip_path = self._create_zip({
            "EP01/Data/Client/Strings_RUS_RU.package": b"PAYLOAD_TEST",
            "EP01/ClientDeltaBuild0.package": b"DELTA_TEST",
        }, "no_unpack.zip")

        report = self.inspector.inspect_archive(zip_path, target_dir=target_dir)
        self.assertTrue(report.integrity_passed)

        # Verify target directory is unchanged (0 files extracted)
        after_entries = os.listdir(target_dir)
        self.assertEqual(before_entries, after_entries)

    def test_scenario_14_repeat_inspection_idempotence(self):
        """Scenario 14: Inspecting the same archive multiple times produces deterministic results."""
        zip_path = self._create_zip({
            "GP03/Data/gp03.package": b"GP03_DATA",
        }, "repeat.zip")

        report1 = self.inspector.inspect_archive(zip_path)
        report2 = self.inspector.inspect_archive(zip_path)

        self.assertEqual(report1.detected_dlc_code, report2.detected_dlc_code)
        self.assertEqual(report1.total_files, report2.total_files)
        self.assertEqual(report1.total_uncompressed_bytes, report2.total_uncompressed_bytes)
        self.assertEqual(report1.safe_relative_paths, report2.safe_relative_paths)

    def test_prohibited_binaries_in_dlc_archive_blocked(self):
        """Prohibited executables (.exe, .dll, .bat) in regular DLC archives raise BlockedBinaryError."""
        bad_binaries = ["hack.exe", "script.bat", "library.dll", "cmd.cmd", "payload.vbs"]
        for bad_bin in bad_binaries:
            with self.subTest(binary=bad_bin):
                bad_zip = self._create_zip({
                    f"EP01/{bad_bin}": b"MALICIOUS_BINARY_BYTES",
                    "EP01/data.package": b"VALID_PKG",
                }, f"bad_{bad_bin}.zip")
                with self.assertRaises(BlockedBinaryError):
                    self.inspector.inspect_archive(bad_zip, is_helper_archive=False)

    def test_helper_archive_allows_trusted_binaries(self):
        """Helper archive (is_helper_archive=True) permits utility executable files per Section 10.3."""
        helper_zip = self._create_zip({
            "DLC_Helper.exe": b"TRUSTED_HELPER_EXE_PAYLOAD",
            "helper.dll": b"TRUSTED_HELPER_DLL_PAYLOAD",
            "config.ini": b"[Settings]\nauto=1",
        }, "dlc_helper.zip")

        report = self.inspector.inspect_archive(helper_zip, is_helper_archive=True)
        self.assertTrue(report.integrity_passed)
        self.assertEqual(report.total_files, 3)

    def test_dlc_nesting_normalization(self):
        """
        Nested wrapper directories (e.g. 'The Sims 4/EP01/Data' or 'EP01/EP01/Data')
        are normalized to start from DLC code root ('EP01/Data').
        """
        zip_nested = self._create_zip({
            "The Sims 4 DLC Repack/EP01/Data/Client/Strings_RUS_RU.package": b"RUS",
            "The Sims 4 DLC Repack/EP01/ClientDeltaBuild0.package": b"DELTA",
        }, "nested.zip")

        report = self.inspector.inspect_archive(zip_nested)
        self.assertEqual(report.detected_dlc_code, "EP01")
        # Check normalized destination paths
        norm_values = list(report.safe_relative_paths.values())
        self.assertIn("EP01/Data/Client/Strings_RUS_RU.package", norm_values)
        self.assertIn("EP01/ClientDeltaBuild0.package", norm_values)

    def test_reserved_windows_device_names_blocked(self):
        """Windows reserved device names ('CON', 'NUL', 'PRN', 'AUX', 'COM1', etc.) are blocked."""
        bad_device_names = ["CON", "NUL", "PRN", "AUX", "COM1.txt", "lpt1.package"]
        for dev in bad_device_names:
            with self.subTest(device=dev):
                with self.assertRaises(PathTraversalError):
                    validate_safe_path(f"EP01/{dev}")


if __name__ == "__main__":
    unittest.main()

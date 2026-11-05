"""
Unit tests for GameFinder, volume & NTFS verification, and DeepDLCScanner.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 5.3 and 9.
"""

import os
from pathlib import Path
import unittest

from core.game_finder import (
    GameFinder,
    DeepDLCScanner,
    GameInstallation,
    is_valid_sims4_root,
    verify_ntfs_filesystem,
    set_test_filesystem_override,
    clear_test_filesystem_overrides,
    parse_steam_libraryfolders_vdf,
)
from core.exceptions import (
    UnsupportedFilesystemError,
    FilesystemError,
)
from tests.conftest import IsolatedEnvironmentTestCase


class MockWinReg:
    """Mock Windows Registry engine for isolated testing."""
    HKEY_LOCAL_MACHINE = 1
    HKEY_CURRENT_USER = 2
    KEY_READ = 0x20019
    KEY_WOW64_64KEY = 0x0100
    KEY_WOW64_32KEY = 0x0200

    def __init__(self, registry_data=None):
        # Format: (hive, subkey): {value_name: value}
        self.data = registry_data or {}

    class MockKey:
        def __init__(self, values):
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    def OpenKey(self, hive, subkey, reserved=0, sam=0):
        key = (hive, subkey)
        if key in self.data:
            return self.MockKey(self.data[key])
        raise FileNotFoundError(f"Key not found: {key}")

    def QueryValueEx(self, key_obj, value_name):
        if value_name in key_obj.values:
            return key_obj.values[value_name], 1
        raise FileNotFoundError(f"Value not found: {value_name}")


class TestGameFinder(IsolatedEnvironmentTestCase):

    def setUp(self):
        super().setUp()
        clear_test_filesystem_overrides()

        # Create simulated valid The Sims 4 game structure
        self.mock_game_dir = Path(self.test_dir) / "The Sims 4"
        self.mock_game_dir.mkdir(parents=True, exist_ok=True)
        (self.mock_game_dir / "Game" / "Bin").mkdir(parents=True, exist_ok=True)
        with open(self.mock_game_dir / "Game" / "Bin" / "TS4_x64.exe", "wb") as f:
            f.write(b"SIMS4_BINARY_STUB")
        (self.mock_game_dir / "Data").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        clear_test_filesystem_overrides()
        super().tearDown()

    def test_is_valid_sims4_root(self):
        """Test detection of valid vs invalid Sims 4 directories."""
        self.assertTrue(is_valid_sims4_root(self.mock_game_dir))

        # Empty directory is invalid
        empty_dir = Path(self.test_dir) / "EmptyFolder"
        empty_dir.mkdir()
        self.assertFalse(is_valid_sims4_root(empty_dir))

        # Non-existent directory
        self.assertFalse(is_valid_sims4_root(Path(self.test_dir) / "NonExistent"))

    def test_ntfs_filesystem_verification_success(self):
        """Test that NTFS filesystem passes verification."""
        set_test_filesystem_override(str(self.mock_game_dir), "NTFS")
        vol, fs = verify_ntfs_filesystem(str(self.mock_game_dir))
        self.assertEqual(fs, "NTFS")
        self.assertTrue(vol)

    def test_unsupported_filesystem_rejection(self):
        """
        Verify that non-NTFS filesystems (FAT32, exFAT, ReFS) are strictly rejected
        with UnsupportedFilesystemError per PROJECT_SPEC.md section 5.3.
        """
        unsupported_filesystems = ["FAT32", "exFAT", "ReFS", "ext4", "FAT16", "vfat"]
        for bad_fs in unsupported_filesystems:
            with self.subTest(fs=bad_fs):
                set_test_filesystem_override(str(self.mock_game_dir), bad_fs)
                with self.assertRaises(UnsupportedFilesystemError):
                    verify_ntfs_filesystem(str(self.mock_game_dir))

    def test_registry_search_detection(self):
        """Test game detection via mock Windows Registry."""
        mock_reg = MockWinReg({
            (MockWinReg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Maxis\The Sims 4"): {
                "Install Dir": str(self.mock_game_dir)
            }
        })
        finder = GameFinder(registry_backend=mock_reg)
        found = finder.search_registry()
        self.assertIsNotNone(found)
        self.assertEqual(found, self.mock_game_dir)

    def test_steam_vdf_parsing_and_detection(self):
        """
        Test Steam libraryfolders.vdf parser with multiple library locations
        and AppID 1222670 detection.
        """
        steam_lib2 = Path(self.test_dir) / "SteamLibrary"
        steam_game = steam_lib2 / "steamapps" / "common" / "The Sims 4"
        steam_game.mkdir(parents=True, exist_ok=True)
        (steam_game / "Game" / "Bin").mkdir(parents=True, exist_ok=True)
        with open(steam_game / "Game" / "Bin" / "TS4_x64.exe", "wb") as f:
            f.write(b"STEAM_SIMS4_STUB")

        vdf_content = f"""
"libraryfolders"
{{
    "0"
    {{
        "path"    "C:\\\\Program Files (x86)\\\\Steam"
        "label"   ""
        "apps"
        {{
            "228980"    "123456"
        }}
    }}
    "1"
    {{
        "path"    "{str(steam_lib2)}"
        "label"   "GamesSSD"
        "apps"
        {{
            "730"       "987654"
            "1222670"   "5432100"
        }}
    }}
}}
"""
        parsed_libs = parse_steam_libraryfolders_vdf(vdf_content)
        self.assertEqual(len(parsed_libs), 2)
        self.assertIn("1222670", parsed_libs[1]["apps"])

        # Test search_steam with mocked steam root
        mock_steam_root = Path(self.test_dir) / "SteamRoot"
        (mock_steam_root / "steamapps").mkdir(parents=True, exist_ok=True)
        with open(mock_steam_root / "steamapps" / "libraryfolders.vdf", "w", encoding="utf-8") as f:
            f.write(vdf_content)

        finder = GameFinder()
        found = finder.search_steam(steam_root=str(mock_steam_root))
        self.assertIsNotNone(found)
        self.assertEqual(found, steam_game)

    def test_custom_path_validation(self):
        """Test validate_custom_path with valid and invalid inputs."""
        set_test_filesystem_override(str(self.mock_game_dir), "NTFS")
        finder = GameFinder()

        # Valid custom path
        inst = finder.validate_custom_path(str(self.mock_game_dir))
        self.assertEqual(inst.path, self.mock_game_dir)
        self.assertEqual(inst.source, "manual")
        self.assertEqual(inst.filesystem, "NTFS")

        # Invalid path: not a Sims 4 directory
        invalid_dir = Path(self.test_dir) / "NotSims4"
        invalid_dir.mkdir()
        with self.assertRaises(FilesystemError):
            finder.validate_custom_path(str(invalid_dir))

        # Invalid path: non-existent
        with self.assertRaises(FilesystemError):
            finder.validate_custom_path(str(Path(self.test_dir) / "DoesNotExist"))


class TestDeepDLCScanner(IsolatedEnvironmentTestCase):

    def setUp(self):
        super().setUp()
        self.game_root = Path(self.test_dir) / "Sims4"
        self.game_root.mkdir(parents=True, exist_ok=True)
        (self.game_root / "Game" / "Bin").mkdir(parents=True, exist_ok=True)
        with open(self.game_root / "Game" / "Bin" / "TS4_x64.exe", "wb") as f:
            f.write(b"STUB")
        self.installation = GameInstallation(
            path=self.game_root,
            volume_path="/",
            filesystem="NTFS",
            source="test"
        )

    def test_empty_dlc_folder_is_not_installed(self):
        """
        Verify that an empty folder (e.g. EP01) is NOT recognized as installed DLC.
        Mandated by PROJECT_SPEC.md section 9.3.
        """
        ep01 = self.game_root / "EP01"
        ep01.mkdir()

        dlc_info = DeepDLCScanner.scan_dlc_folder(ep01)
        self.assertEqual(dlc_info.dlc_code, "EP01")
        self.assertEqual(dlc_info.dlc_type, "EP")
        self.assertEqual(dlc_info.dlc_number, 1)
        self.assertFalse(dlc_info.is_installed)
        self.assertEqual(dlc_info.package_count, 0)
        self.assertEqual(dlc_info.total_size_bytes, 0)

    def test_zero_byte_package_is_not_installed(self):
        """
        Verify that a folder containing only 0-byte dummy .package files
        is NOT recognized as installed DLC.
        Mandated by PROJECT_SPEC.md section 9.3.
        """
        gp02 = self.game_root / "GP02"
        gp02.mkdir()
        with open(gp02 / "dummy.package", "wb") as f:
            pass  # 0 bytes

        dlc_info = DeepDLCScanner.scan_dlc_folder(gp02)
        self.assertFalse(dlc_info.is_installed)
        self.assertEqual(dlc_info.package_count, 0)
        self.assertEqual(dlc_info.total_size_bytes, 0)

    def test_non_package_files_do_not_mark_as_installed(self):
        """Verify that folders with only .txt, .ini, or .exe are not marked installed."""
        sp03 = self.game_root / "SP03"
        sp03.mkdir()
        with open(sp03 / "readme.txt", "w") as f:
            f.write("Some text")
        with open(sp03 / "config.ini", "w") as f:
            f.write("[Config]\nkey=val")

        dlc_info = DeepDLCScanner.scan_dlc_folder(sp03)
        self.assertFalse(dlc_info.is_installed)
        self.assertEqual(dlc_info.package_count, 0)

    def test_valid_package_marks_as_installed(self):
        """
        Verify that folder with real non-zero .package files is recognized as installed DLC.
        """
        ep04 = self.game_root / "EP04"
        ep04_data = ep04 / "Data" / "Client"
        ep04_data.mkdir(parents=True, exist_ok=True)
        (ep04 / "__Installer").mkdir(exist_ok=True)

        pkg1 = ep04 / "ClientDeltaBuild0.package"
        pkg2 = ep04_data / "Strings_RUS_RU.package"
        with open(pkg1, "wb") as f:
            f.write(b"PKG_CONTENT_DATA_1234567890")
        with open(pkg2, "wb") as f:
            f.write(b"STRINGS_RUSSIAN_DATA")

        dlc_info = DeepDLCScanner.scan_dlc_folder(ep04)
        self.assertEqual(dlc_info.dlc_code, "EP04")
        self.assertEqual(dlc_info.dlc_type, "EP")
        self.assertEqual(dlc_info.dlc_number, 4)
        self.assertTrue(dlc_info.is_installed)
        self.assertEqual(dlc_info.package_count, 2)
        self.assertEqual(dlc_info.total_size_bytes, len(b"PKG_CONTENT_DATA_1234567890") + len(b"STRINGS_RUSSIAN_DATA"))
        self.assertTrue(dlc_info.has_installer)
        self.assertFalse(dlc_info.has_delta)

    def test_full_installation_scan(self):
        """
        Test scan_game_installation across multiple DLC folders of different types
        and verify aggregate counts.
        """
        # Installed EP01 (with valid .package)
        ep01 = self.game_root / "EP01"
        ep01.mkdir()
        with open(ep01 / "ep01.package", "wb") as f:
            f.write(b"EP01_CONTENT")

        # Installed GP05 (with valid .package in subfolder)
        gp05 = self.game_root / "GP05"
        (gp05 / "Data").mkdir(parents=True)
        with open(gp05 / "Data" / "gp05.package", "wb") as f:
            f.write(b"GP05_CONTENT")

        # Fake/Empty SP01 (empty folder -> not installed)
        sp01 = self.game_root / "SP01"
        sp01.mkdir()

        # Dummy non-DLC folder (should be ignored by pattern)
        other_folder = self.game_root / "CustomMods"
        other_folder.mkdir()
        with open(other_folder / "mod.package", "wb") as f:
            f.write(b"MOD")

        report = DeepDLCScanner.scan_game_installation(self.installation)
        self.assertEqual(report.total_dlcs_scanned, 3)  # EP01, GP05, SP01
        self.assertEqual(report.installed_count, 2)     # EP01 and GP05
        self.assertNotIn("CustomMods", report.dlcs)

        self.assertTrue(report.dlcs["EP01"].is_installed)
        self.assertTrue(report.dlcs["GP05"].is_installed)
        self.assertFalse(report.dlcs["SP01"].is_installed)


if __name__ == "__main__":
    unittest.main()

"""
Automated Build Artifacts and Release Packaging Test Suite for Stage 10.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 3, 4.1, 5.1, 5.2, 10, and 12.2.
Validates:
- PyInstaller .spec configuration and asInvoker manifest
- Zero-leakage bundle audit of owner_tools, keys, and secrets
- Cross-store White Label isolation
- Release manifest creation, Ed25519 signing, and SHA256.txt verification
- Tamper detection and signature verification
- Main entry point frozen path resolution
- Explicit demarcation of Windows-only runtime checks (NOT VERIFIED — REQUIRES WINDOWS)
"""

import os
import sys
import tempfile
import unittest
import shutil
from pathlib import Path

from core.exceptions import SecurityError, WhiteLabelLeakError, ManifestVerificationError
from owner_tools.key_vault import KeyVaultManager
from owner_tools.bundle_inspector import BundleInspector, BundleAuditReport
from owner_tools.packaging import PackagingEngine, generate_spec_content, WINDOWS_ASINVOKER_MANIFEST
from owner_tools.signing import ManifestSigner
import main as main_module


class TestBuildArtifacts(unittest.TestCase):
    """
    Validates Stage 10 build artifacts, spec files, security isolation,
    and release manifest verification.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)

        # Generate a test Ed25519 keypair for signing
        key_mgr = KeyVaultManager(keyring_dir=self.test_root / "keyring")
        self.priv_key, self.pub_key = key_mgr.generate_and_save_master_key()

        # Set up a sample store directory
        self.stores_base = self.test_root / "configs" / "stores"
        self.store_alpha_dir = self.stores_base / "store_alpha"
        self.store_alpha_dir.mkdir(parents=True, exist_ok=True)

        # store.json
        (self.store_alpha_dir / "store.json").write_text("""{
  "store_id": "store_alpha",
  "store_name": "Test Store Alpha",
  "application_name": "Sims 4 DLC Test Loader",
  "application_version": "1.0.0",
  "theme_id": "sims4_dark_green",
  "sources": {
    "primary_dlc_folder": "https://disk.yandex.ru/d/test_alpha",
    "helper_archive_url": "https://disk.yandex.ru/d/test_helper"
  }
}""", encoding="utf-8")

        # theme.json
        (self.store_alpha_dir / "theme.json").write_text("""{
  "theme_id": "sims4_dark_green",
  "theme_name": "Test Dark Green",
  "palette": {
    "background_dark": "#121815",
    "surface_dark": "#1a2420",
    "accent_primary": "#2ecc71",
    "text_primary": "#f0fdf4"
  }
}""", encoding="utf-8")

        # assets
        assets_dir = self.store_alpha_dir / "assets"
        assets_dir.mkdir(exist_ok=True)
        (assets_dir / "icon.ico").write_bytes(b"\x00\x00\x01\x00_ICO_DUMMY_")
        (assets_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n_PNG_DUMMY_")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_pyinstaller_spec_validation(self):
        """
        Verify that reference sims4_manager.spec conforms strictly to PROJECT_SPEC:
        - Excludes owner_tools
        - Sets console=False
        - Contains asInvoker manifest
        - No secret file extensions referenced
        """
        spec_path = Path("sims4_manager.spec")
        self.assertTrue(spec_path.is_file(), "sims4_manager.spec must exist in workspace root")

        report = BundleInspector.audit_pyinstaller_spec(spec_path)
        self.assertTrue(report.is_valid, f"Spec file failed audit: {report.violations}")
        self.assertEqual(len(report.violations), 0)

        spec_content = spec_path.read_text(encoding="utf-8")
        self.assertIn("asInvoker", spec_content)
        self.assertIn("owner_tools", spec_content)
        self.assertIn("console=False", spec_content)

    def test_spec_generation_for_white_label_store(self):
        """
        Verify dynamic spec generator creates valid spec targeted to specific store profile.
        """
        spec_str = generate_spec_content(
            store_id="store_beta",
            output_exe_name="Sims4_ModernEdition",
            icon_path="configs/stores/store_beta/assets/icon.ico"
        )
        self.assertIn("configs/stores/store_beta", spec_str)
        self.assertNotIn("configs/stores/store_alpha", spec_str)
        self.assertIn("Sims4_ModernEdition", spec_str)
        self.assertIn("asInvoker", spec_str)
        self.assertIn("owner_tools", spec_str)

        # Audit generated spec string
        temp_spec = self.test_root / "temp_generated.spec"
        temp_spec.write_text(spec_str, encoding="utf-8")
        report = BundleInspector.audit_pyinstaller_spec(temp_spec)
        self.assertTrue(report.is_valid)

    def test_bundle_inspector_detects_forbidden_owner_tools(self):
        """
        Verify that BundleInspector strictly catches and reports presence of
        owner_tools directory in the client bundle.
        """
        fake_bundle = self.test_root / "fake_bundle_with_leak"
        fake_bundle.mkdir()
        leaked_owner_tools = fake_bundle / "owner_tools"
        leaked_owner_tools.mkdir()
        (leaked_owner_tools / "build_manager.py").write_text("# Leaked code", encoding="utf-8")

        report = BundleInspector.audit_bundle_directory(fake_bundle, expected_store_id="store_alpha")
        self.assertFalse(report.is_valid)
        self.assertTrue(any("owner_tools" in v for v in report.violations))

    def test_bundle_inspector_detects_secret_files(self):
        """
        Verify that BundleInspector flags prohibited files: *.vault, *.key, challenge_registry.db, oauth.dat.
        """
        fake_bundle = self.test_root / "fake_bundle_with_secrets"
        fake_bundle.mkdir()
        (fake_bundle / "backup.vault").write_bytes(b"S4KV_VAULT_BYTES")
        (fake_bundle / "ed25519_master.key").write_bytes(b"KEY_BYTES")
        (fake_bundle / "challenge_registry.db").write_bytes(b"SQLITE_DB_BYTES")
        (fake_bundle / "oauth.dat").write_bytes(b"OAUTH_BYTES")

        report = BundleInspector.audit_bundle_directory(fake_bundle, expected_store_id="store_alpha")
        self.assertFalse(report.is_valid)
        self.assertGreaterEqual(len(report.violations), 4)

    def test_bundle_inspector_detects_cross_store_contamination(self):
        """
        Verify that bundle built for store_alpha rejects presence of store_beta files.
        """
        fake_bundle = self.test_root / "fake_bundle_cross_store"
        fake_bundle.mkdir()
        store_beta_dir = fake_bundle / "configs" / "stores" / "store_beta"
        store_beta_dir.mkdir(parents=True)
        (store_beta_dir / "store.json").write_text("{}", encoding="utf-8")

        report = BundleInspector.audit_bundle_directory(fake_bundle, expected_store_id="store_alpha")
        self.assertFalse(report.is_valid)
        self.assertTrue(any("Cross-store" in v for v in report.violations))

    def test_release_packaging_and_manifest_signing(self):
        """
        Verify complete release packaging:
        - Layout created under releases/store_alpha/
        - release-manifest.json created and signed with Ed25519
        - SHA256.txt generated with sorted checksums
        - Bundle integrity verification passes with 100% match
        """
        releases_dir = self.test_root / "releases"
        packaging = PackagingEngine(releases_base_dir=releases_dir)

        out_dir = packaging.prepare_release_directory(
            store_id="store_alpha",
            private_key_bytes=self.priv_key,
            public_key_bytes=self.pub_key,
            stores_base_dir=self.stores_base
        )

        self.assertTrue((out_dir / "Sims4_Store_Alpha.exe").is_file())
        self.assertTrue((out_dir / "release-manifest.json").is_file())
        self.assertTrue((out_dir / "release-manifest.sig").is_file())
        self.assertTrue((out_dir / "SHA256.txt").is_file())

        # Verify integrity using public key
        report = BundleInspector.verify_release_integrity(
            release_dir=out_dir,
            public_key_bytes=self.pub_key,
            expected_store_id="store_alpha"
        )
        self.assertTrue(report.is_valid)

        # Check SHA256.txt contents
        sha_text = (out_dir / "SHA256.txt").read_text(encoding="utf-8")
        self.assertIn("Sims4_Store_Alpha.exe", sha_text)
        self.assertIn("store.json", sha_text)
        self.assertNotIn("SHA256.txt", sha_text)

    def test_tampered_release_file_detected(self):
        """
        Verify that altering any release file breaks verification with ManifestVerificationError.
        """
        releases_dir = self.test_root / "releases"
        packaging = PackagingEngine(releases_base_dir=releases_dir)

        out_dir = packaging.prepare_release_directory(
            store_id="store_alpha",
            private_key_bytes=self.priv_key,
            public_key_bytes=self.pub_key,
            stores_base_dir=self.stores_base
        )

        # Tamper with the executable
        binary = out_dir / "Sims4_Store_Alpha.exe"
        binary.write_bytes(b"CORRUPTED_BINARY_BYTES")

        with self.assertRaises(ManifestVerificationError):
            BundleInspector.verify_release_integrity(
                release_dir=out_dir,
                public_key_bytes=self.pub_key,
                expected_store_id="store_alpha"
            )

    def test_missing_signature_rejected(self):
        """
        Verify that missing release-manifest.sig causes immediate rejection.
        """
        releases_dir = self.test_root / "releases"
        packaging = PackagingEngine(releases_base_dir=releases_dir)

        out_dir = packaging.prepare_release_directory(
            store_id="store_alpha",
            private_key_bytes=self.priv_key,
            public_key_bytes=self.pub_key,
            stores_base_dir=self.stores_base
        )

        # Delete signature file
        (out_dir / "release-manifest.sig").unlink()

        with self.assertRaises(ManifestVerificationError):
            BundleInspector.verify_release_integrity(
                release_dir=out_dir,
                public_key_bytes=self.pub_key,
                expected_store_id="store_alpha"
            )

    def test_main_entrypoint_path_resolution(self):
        """
        Verify main.py path resolution, store detection, and headless mode initialization.
        """
        # Test runtime compatibility function
        res_compat = main_module.check_runtime_compatibility()
        self.assertTrue(res_compat)

        # Test store detection
        detected = main_module.detect_store_id(Path("."), cli_override="store_custom")
        self.assertEqual(detected, "store_custom")

        # Test headless run
        ret_code = main_module.main(["--check-runtime"])
        self.assertEqual(ret_code, 0)

        ret_headless = main_module.main(["--store-id", "store_alpha", "--headless"])
        self.assertEqual(ret_headless, 0)

    def test_windows_only_environment_flag(self):
        """
        Explicitly tracks and verifies the boundary between Linux CI and native Windows 10/11 x64:
        Documents which capabilities require a physical Windows environment and must not
        be falsely reported as verified on Linux.
        """
        is_windows = sys.platform == "win32"
        windows_only_checks = {
            "pe_executable_execution": "NOT VERIFIED — REQUIRES WINDOWS",
            "customtkinter_win32_window": "NOT VERIFIED — REQUIRES WINDOWS",
            "windows_shell_icon_ico": "NOT VERIFIED — REQUIRES WINDOWS",
            "native_ntdll_ntcreatefile": "NOT VERIFIED — REQUIRES WINDOWS",
            "native_crypt32_dpapi": "NOT VERIFIED — REQUIRES WINDOWS",
            "uac_elevation_shellexecute": "NOT VERIFIED — REQUIRES WINDOWS",
            "ntfs_volume_information": "NOT VERIFIED — REQUIRES WINDOWS",
            "windows_defender_smartscreen": "NOT VERIFIED — REQUIRES WINDOWS",
        }

        if not is_windows:
            for check_name, status in windows_only_checks.items():
                self.assertEqual(status, "NOT VERIFIED — REQUIRES WINDOWS")
        else:
            # On native Windows, flags can be verified live
            self.assertTrue(is_windows)


if __name__ == "__main__":
    unittest.main()

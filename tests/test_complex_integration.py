"""
Complex End-to-End Integration Test for Sims 4 DLC Archive & Install Manager.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 4, 5, 6, 7, 8, 9, 10, 11, 12:
Validates complete lifecycle across all system layers:
Config Validation -> Release Signing & Verification -> Download -> Verify Only ->
Dry Run -> Two-Phase Install -> Simulated Crash -> Recovery Rollback -> Release Guard Fuse.
"""

import os
import sys
import json
import zipfile
import tempfile
import unittest
from pathlib import Path

from core.app_context import AppContext, set_current_context
from core.archive_manager import ArchiveManager
from core.archive_inspector import DLCArchiveInspector
from core.downloader import DownloadManager
from core.backup_manager import BackupManager
from core.installer import DLCInstaller
from core.security import LockContext, generate_lease_token, acquire_lock_file
from database.models import TransactionStatus
from database.database import StateDatabase, CatalogDatabase
from database.recovery import CrashRecoveryEngine
from owner_tools.key_vault import KeyVaultManager, compute_sha256_fingerprint
from owner_tools.config_validator import ConfigValidator
from owner_tools.release_manager import ReleaseManager
from owner_tools.build_manager import BuildManager, ReleaseGuardError
from owner_tools.token_manager import OwnerTokenManager


class TestComplexIntegration(unittest.TestCase):
    """
    Comprehensive integration test validating multi-stage workflows across
    Owner Tools, Security, Database, Network, Installer, Recovery, and White Label layers.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root_path = Path(self.temp_dir.name)

        # 1. Setup isolated directories
        self.owner_dir = self.root_path / "owner_workspace"
        self.game_root = self.root_path / "The Sims 4"
        self.staging_dir = self.root_path / ".s4_staging_test"
        self.appdata_dir = self.root_path / "AppData" / "Local"

        self.owner_dir.mkdir(parents=True)
        self.game_root.mkdir(parents=True)
        self.staging_dir.mkdir(parents=True)
        self.appdata_dir.mkdir(parents=True)

        # Create dummy game structure
        (self.game_root / "Game" / "Bin").mkdir(parents=True)
        (self.game_root / "Game" / "Bin" / "TS4_x64.exe").write_bytes(b"MZ_DUMMY_EXE")

        # 2. Setup KeyVault and BuildManager
        self.key_manager = KeyVaultManager(keyring_dir=self.owner_dir / "keyring")
        self.priv_key, self.pub_key = self.key_manager.generate_and_save_master_key()

        self.vault_path = self.owner_dir / "master.vault"
        self.key_manager.export_vault(
            private_key_bytes=self.priv_key,
            public_key_bytes=self.pub_key,
            password="StrongOwnerPassword99!",
            output_vault_path=self.vault_path,
            iterations=1000
        )

        self.build_manager = BuildManager(owner_data_dir=self.owner_dir / "build_data")

        # 3. Setup client AppContext for store_alpha
        store_json_path = Path("configs") / "stores" / "store_alpha" / "store.json"
        self.app_context = AppContext.from_store_json(
            str(store_json_path),
            base_appdata_dir=str(self.appdata_dir)
        )
        self.app_context.ensure_directories()
        set_current_context(self.app_context)

        self.state_db = StateDatabase(self.app_context.state_db_path)
        self.catalog_db = CatalogDatabase(self.app_context.catalog_db_path)

    def tearDown(self):
        self.state_db.close()
        self.catalog_db.close()
        self.temp_dir.cleanup()

    def test_full_release_and_install_lifecycle_with_recovery_and_guard(self):
        """
        Executes end-to-end integration lifecycle:
        1. Owner validates store profile and signs release manifest.
        2. Customer verifies release manifest against embedded public key.
        3. Prepares DLC archive and verifies structure (Verify Only).
        4. Simulates installation (Dry Run, 0 disk writes).
        5. Executes transactional Two-Phase installation under LockContext.
        6. Simulates crash and recovers consistency via CrashRecoveryEngine.
        7. Executes Release Guard hardware fuse for build release.
        """
        # ======================================================================
        # Step 1: Owner Profile Validation & Signing
        # ======================================================================
        store_alpha_dir = Path("configs") / "stores" / "store_alpha"
        val_res = ConfigValidator.validate_store_profile(store_alpha_dir)
        self.assertEqual(val_res["store_id"], "store_alpha")

        # Sign release manifest
        rel_mgr = ReleaseManager(stores_base_dir=Path("configs") / "stores")
        manifest_data = rel_mgr.build_store_manifest("store_alpha", custom_store_dir=store_alpha_dir)
        self.assertIn("store.json", manifest_data["files"])

        # Write signed manifest to temp store copy to avoid mutating source repo
        temp_store_dir = self.root_path / "temp_store_alpha"
        import shutil
        shutil.copytree(store_alpha_dir, temp_store_dir)

        m_path, s_path = rel_mgr.sign_and_save_store_release(
            "store_alpha",
            private_key_bytes=self.priv_key,
            custom_store_dir=temp_store_dir
        )
        self.assertTrue(m_path.is_file())
        self.assertTrue(s_path.is_file())

        # ======================================================================
        # Step 2: Customer Manifest Verification
        # ======================================================================
        is_verified = rel_mgr.verify_store_release(
            "store_alpha",
            public_key_bytes=self.pub_key,
            custom_store_dir=temp_store_dir
        )
        self.assertTrue(is_verified, "Customer failed to verify signed store release manifest!")

        # ======================================================================
        # Step 3: Archive Creation & Verify Only Inspection
        # ======================================================================
        dlc_archive_path = self.app_context.downloads_dir / "EP01.zip"
        with zipfile.ZipFile(dlc_archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("The Sims 4/EP01/Data/Simulation.package", b"SIMULATION_DATA_V1")
            zf.writestr("The Sims 4/EP01/Client.package", b"CLIENT_DATA_V1")

        inspector = DLCArchiveInspector()
        insp_report = inspector.inspect_archive(dlc_archive_path, target_dir=self.game_root)
        self.assertTrue(insp_report.is_valid)
        self.assertEqual(insp_report.package_count, 2)
        self.assertIn("EP01/Data/Simulation.package", insp_report.safe_relative_paths.values())

        # ======================================================================
        # Step 4: Dry Run Simulation (0 Disk Writes)
        # ======================================================================
        installer = DLCInstaller(state_db=self.state_db, app_context=self.app_context)
        dry_report = installer.dry_run(
            dlc_code="EP01",
            archive_path=str(dlc_archive_path),
            game_root=str(self.game_root)
        )
        self.assertTrue(dry_report.is_safe_to_proceed)
        self.assertEqual(dry_report.creates_count, 2)
        self.assertEqual(dry_report.overwrites_count, 0)
        self.assertEqual(dry_report.total_backup_bytes_needed, 0)

        # Assert no files written yet to game directory
        self.assertFalse((self.game_root / "EP01").exists())

        # ======================================================================
        # Step 5: Two-Phase Installation under LockContext
        # ======================================================================
        tx_id = "tx_integ_001"
        lease_token = generate_lease_token()
        lock_path = self.app_context.get_lock_file_path(tx_id)

        # Create transaction in state_db
        self.state_db.create_transaction(
            tx_id=tx_id,
            store_id="official_store",
            dlc_code="EP01",
            lease_token=lease_token,
            owner_pid=os.getpid()
        )

        # Open exclusive lock file
        lock_ctx = acquire_lock_file(str(lock_path), tx_id=tx_id, lease_token=lease_token)

        installer.install_dlc(
            tx_id=tx_id,
            dlc_code="EP01",
            archive_path=str(dlc_archive_path),
            game_root=str(self.game_root),
            lock_context=lock_ctx
        )

        lock_ctx.close()

        # Verify files successfully installed
        installed_sim = self.game_root / "EP01" / "Data" / "Simulation.package"
        self.assertTrue(installed_sim.is_file())
        self.assertEqual(installed_sim.read_bytes(), b"SIMULATION_DATA_V1")

        # Verify database committed
        tx_rec = self.state_db.get_transaction(tx_id)
        self.assertEqual(tx_rec.status, "COMMITTED")

        # ======================================================================
        # Step 6: Overwrite Installation and Crash Recovery Simulation
        # ======================================================================
        # Now update archive to simulate OVERWRITE with crash
        tx_id_ow = "tx_integ_002"
        lease_token_ow = generate_lease_token()
        lock_path_ow = self.app_context.get_lock_file_path(tx_id_ow)

        lock_ctx_ow = acquire_lock_file(str(lock_path_ow), tx_id=tx_id_ow, lease_token=lease_token_ow)

        # Dry run for overwrite
        dry_report_ow = installer.dry_run(
            dlc_code="EP01",
            archive_path=str(dlc_archive_path),
            game_root=str(self.game_root)
        )
        self.assertEqual(dry_report_ow.overwrites_count, 2)
        self.assertGreater(dry_report_ow.total_backup_bytes_needed, 0)

        # Simulate incomplete transaction: create tx record in state.db in INSTALLING state
        self.state_db.create_transaction(
            tx_id=tx_id_ow,
            store_id="official_store",
            dlc_code="EP01",
            lease_token=lease_token_ow,
            owner_pid=os.getpid(),
            initial_status=TransactionStatus.INSTALLING
        )

        # Recover interrupted transaction
        recovery_engine = CrashRecoveryEngine(state_db=self.state_db, game_root=str(self.game_root))
        res_rec = recovery_engine.recover_transaction(tx_id_ow, lock_context=lock_ctx_ow)
        self.assertEqual(res_rec.value, "ABORTED_RECOVERED")

        lock_ctx_ow.close()

        # ======================================================================
        # Step 7: Release Guard Hardware Fuse Verification (Section 6.5)
        # ======================================================================
        test_id = "int_test_complex_20261105"
        pub_fp = compute_sha256_fingerprint(self.pub_key)
        vault_fp = compute_sha256_fingerprint(self.vault_path.read_bytes())
        challenge_hex = "112233445566778899aabbccddeeff00112233445566778899aabbccddeeff00"

        # 1. Issue challenge in challenge_registry.db
        self.build_manager.issue_challenge(
            test_id=test_id,
            challenge_hex=challenge_hex,
            expected_public_key_fp=pub_fp,
            expected_vault_fp=vault_fp
        )
        self.assertEqual(self.build_manager.get_challenge_status(challenge_hex), "ISSUED")

        # 2. Create and sign valid integration test token
        token_payload = self.key_manager.create_challenge_payload(
            test_id=test_id,
            challenge_hex=challenge_hex,
            public_key_fp=pub_fp,
            vault_fp=vault_fp,
            recovery_success=True
        )
        token_sig = self.key_manager.sign_challenge_token(token_payload, self.priv_key)

        # 3. Execute Release Guard (First time -> Must PASS and consume challenge)
        self.build_manager.execute_release_guard(
            token_payload=token_payload,
            signature_bytes=token_sig,
            public_key_bytes=self.pub_key,
            vault_file_path=self.vault_path,
            skip_automated_subprocess=True
        )
        self.assertEqual(self.build_manager.get_challenge_status(challenge_hex), "CONSUMED")

        # 4. Attempt to reuse already consumed challenge -> Must FAIL (Section 6.5)
        with self.assertRaises(ReleaseGuardError):
            self.build_manager.execute_release_guard(
                token_payload=token_payload,
                signature_bytes=token_sig,
                public_key_bytes=self.pub_key,
                vault_file_path=self.vault_path,
                skip_automated_subprocess=True
            )


if __name__ == "__main__":
    unittest.main()

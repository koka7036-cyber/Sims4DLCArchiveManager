"""
Automated Unit Tests for Stage 8: Branding, Theme Manager, and White Label GUI Layer.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 5, 11, and 12.
"""

import os
import tempfile
import unittest
from pathlib import Path

from core.app_context import AppContext, set_current_context
from branding.theme_manager import ThemeManager, BUILTIN_PALETTES
from branding.asset_manager import AssetManager
from branding.branding_manager import BrandingManager
from gui.app import Sims4ManagerApp
from gui.controller import AppController, GameNotFoundError
from gui.state import DLCUIItem, DLCStatus, TaskState
from gui.main_window import MainWindow
from gui.navigation import NavigationController
from gui.dialogs.confirmation_dialog import ConfirmationDialog
from gui.dialogs.error_dialog import ErrorDialog
from gui.dialogs.consent_dialog import ConsentDialog
from gui.dialogs.dry_run_dialog import DryRunDialog
from core.exceptions import (
    UnsupportedFilesystemError,
    ReparsePointError,
    PathTraversalError,
    BlockedBinaryError,
    CorruptedArchiveError,
    HashMismatchError,
    YandexRateLimitError,
    NetworkError,
    LockContextError,
    RecoveryConflictError,
)


class TestBrandingAndThemes(unittest.TestCase):
    """
    Validates White Label configuration loading, dynamic palette selection,
    and asset resolution for store_alpha and store_beta.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_theme_manager_builtin_palettes(self):
        """Verify that all standard spec palettes are defined and accessible."""
        for theme_key in ["sims4_dark_green", "modern_cyber_blue", "minimal_pro_slate", "marketplace_emerald"]:
            tm = ThemeManager(theme_key)
            self.assertEqual(tm.theme_id, theme_key)
            self.assertTrue(tm.background_dark.startswith("#"))
            self.assertTrue(tm.accent_primary.startswith("#"))
            self.assertTrue(tm.text_primary.startswith("#"))
            self.assertTrue(tm.danger.startswith("#"))

    def test_theme_manager_from_store_alpha_theme_json(self):
        """Test loading theme.json from store_alpha configuration."""
        theme_path = Path("configs") / "stores" / "store_alpha" / "theme.json"
        self.assertTrue(theme_path.is_file(), f"Missing theme config: {theme_path}")

        tm = ThemeManager.from_theme_json(str(theme_path))
        self.assertEqual(tm.theme_id, "sims4_dark_green")
        self.assertEqual(tm.accent_primary, "#2ecc71")
        self.assertEqual(tm.background_dark, "#121815")

    def test_theme_manager_from_store_beta_theme_json(self):
        """Test loading theme.json from store_beta configuration."""
        theme_path = Path("configs") / "stores" / "store_beta" / "theme.json"
        self.assertTrue(theme_path.is_file(), f"Missing theme config: {theme_path}")

        tm = ThemeManager.from_theme_json(str(theme_path))
        self.assertEqual(tm.theme_id, "modern_cyber_blue")
        self.assertEqual(tm.accent_primary, "#58a6ff")
        self.assertEqual(tm.background_dark, "#0d1117")

    def test_theme_manager_fallback_on_missing_file(self):
        """Verify graceful fallback to default theme if theme file is absent."""
        tm = ThemeManager.from_theme_json(str(self.base_path / "non_existent_theme.json"))
        self.assertEqual(tm.theme_id, "sims4_dark_green")
        self.assertEqual(tm.accent_primary, "#2ecc71")

    def test_asset_manager_graceful_missing_assets(self):
        """Verify AssetManager returns None for missing assets without crashing."""
        am = AssetManager(self.base_path)
        self.assertIsNone(am.get_logo_path())
        self.assertIsNone(am.get_icon_path())
        self.assertIsNone(am.get_banner_path())
        self.assertFalse(am.has_custom_assets())

    def test_branding_manager_store_alpha_isolation(self):
        """Test BrandingManager instantiation for store_alpha profile."""
        store_json = Path("configs") / "stores" / "store_alpha" / "store.json"
        ctx = AppContext.from_store_json(str(store_json), base_appdata_dir=str(self.base_path))
        set_current_context(ctx)

        bm = BrandingManager(ctx)
        self.assertEqual(bm.store_id, "store_alpha")
        self.assertEqual(bm.store_name, "Fanfiriel Games Store")
        self.assertEqual(bm.application_name, "Sims 4 DLC Loader - Fanfiriel Edition")
        self.assertEqual(bm.theme.theme_id, "sims4_dark_green")
        self.assertEqual(bm.theme.accent_primary, "#2ecc71")
        self.assertTrue(bm.is_feature_enabled("dry_run_available"))

    def test_branding_manager_store_beta_isolation(self):
        """Test BrandingManager instantiation for store_beta profile."""
        store_json = Path("configs") / "stores" / "store_beta" / "store.json"
        ctx = AppContext.from_store_json(str(store_json), base_appdata_dir=str(self.base_path))
        set_current_context(ctx)

        bm = BrandingManager(ctx)
        self.assertEqual(bm.store_id, "store_beta")
        self.assertEqual(bm.store_name, "Modern Play Games")
        self.assertEqual(bm.application_name, "Sims 4 DLC Archive Hub - Modern Edition")
        self.assertEqual(bm.theme.theme_id, "modern_cyber_blue")
        self.assertEqual(bm.theme.accent_primary, "#58a6ff")
        self.assertFalse(bm.is_feature_enabled("allow_custom_oauth"))

    def test_zero_hardcoded_store_values_in_branding(self):
        """Verify that dynamic store_id properly decouples names and paths."""
        custom_ctx = AppContext(
            store_id="store_custom_gamma",
            store_name="Gamma Store",
            application_name="Gamma Custom Edition",
            theme_id="minimal_pro_slate",
            base_appdata_dir=str(self.base_path)
        )
        bm = BrandingManager(custom_ctx)
        self.assertEqual(bm.store_id, "store_custom_gamma")
        self.assertEqual(bm.store_name, "Gamma Store")
        self.assertEqual(bm.application_name, "Gamma Custom Edition")
        self.assertEqual(bm.theme.theme_id, "minimal_pro_slate")
        self.assertEqual(bm.theme.accent_primary, "#38bdf8")


class TestGUIComponentsAndFlow(unittest.TestCase):
    """
    Validates GUI construction, navigation, view models, controllers,
    error mapping, and dialogs in headless Linux sandbox.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)

        store_json = Path("configs") / "stores" / "store_alpha" / "store.json"
        self.context = AppContext.from_store_json(str(store_json), base_appdata_dir=str(self.base_path))
        self.context.ensure_directories()
        set_current_context(self.context)
        self.branding = BrandingManager(self.context)
        self.controller = AppController(self.context, self.branding)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_app_controller_state_transitions(self):
        """Test AppController busy/idle/error state transitions and observer notifications."""
        notifications = []
        self.controller.add_listener(lambda: notifications.append(1))

        self.assertFalse(self.controller.operation_state.is_busy)
        self.assertEqual(self.controller.operation_state.task, TaskState.IDLE)

        # Set busy
        self.controller.set_busy(TaskState.SCANNING_GAME, "Scanning...", progress=0.25)
        self.assertTrue(self.controller.operation_state.is_busy)
        self.assertEqual(self.controller.operation_state.task, TaskState.SCANNING_GAME)
        self.assertEqual(self.controller.operation_state.status_message, "Scanning...")
        self.assertEqual(self.controller.operation_state.progress, 0.25)
        self.assertGreater(len(notifications), 0)

        # Set error
        self.controller.set_error("Test failure")
        self.assertFalse(self.controller.operation_state.is_busy)
        self.assertEqual(self.controller.operation_state.last_error, "Test failure")

        # Set idle
        self.controller.set_idle("Ready")
        self.assertFalse(self.controller.operation_state.is_busy)
        self.assertEqual(self.controller.operation_state.status_message, "Ready")

    def test_app_controller_exception_translation(self):
        """Verify that backend exceptions are translated into user-friendly Russian messages."""
        translations = [
            (GameNotFoundError("test"), "Каталог The Sims 4 не найден."),
            (UnsupportedFilesystemError("test"), "Файловая система диска не поддерживается. Требуется том NTFS."),
            (ReparsePointError("test"), "Обнаружена символическая ссылка"),
            (PathTraversalError("test"), "Path Traversal"),
            (BlockedBinaryError("test"), "запрещённые исполняемые файлы"),
            (CorruptedArchiveError("test"), "Архив повреждён"),
            (HashMismatchError("test"), "Контрольная сумма"),
            (YandexRateLimitError("test"), "лимит запросов к облаку"),
            (NetworkError("test"), "Сетевой сбой"),
            (LockContextError("test"), "Ошибка блокировки файловой транзакции"),
            (RecoveryConflictError("test"), "Конфликт восстановления"),
        ]
        for exc, expected_substr in translations:
            msg = self.controller.translate_exception(exc)
            self.assertIn(expected_substr.lower(), msg.lower())

    def test_main_window_and_views_instantiation(self):
        """Verify that MainWindow and all 7 views instantiate and wire successfully."""
        window = MainWindow(self.controller)
        self.assertEqual(window.title(), self.branding.application_name)

        # Verify all 7 views are registered in navigation
        expected_views = ["dashboard", "catalog", "downloads", "inspector", "backups", "history", "settings"]
        for key in expected_views:
            self.assertIn(key, window.navigation.views)

        # Test view switching
        for key in expected_views:
            window.navigation.show_view(key)
            self.assertEqual(window.navigation.current_view_key, key)

        window.quit()

    def test_catalog_view_filtering_and_selection(self):
        """Test CatalogView search, filters, and batch selection without disk writes."""
        # Add test items to controller
        self.controller.dlc_items["EP01"] = DLCUIItem(
            item_id="EP01",
            title="EP01 Get to Work",
            dlc_type="EP",
            archive_name="EP01.zip",
            archive_size=1024 * 1024 * 500,
            status=DLCStatus.AVAILABLE
        )
        self.controller.dlc_items["GP02"] = DLCUIItem(
            item_id="GP02",
            title="GP02 Spa Day",
            dlc_type="GP",
            archive_name="GP02.zip",
            archive_size=1024 * 1024 * 300,
            status=DLCStatus.INSTALLED
        )

        window = MainWindow(self.controller)
        catalog_view = window.view_catalog

        # Batch select all
        catalog_view._select_all()
        self.assertTrue(self.controller.dlc_items["EP01"].selected)
        self.assertTrue(self.controller.dlc_items["GP02"].selected)

        # Batch deselect all
        catalog_view._deselect_all()
        self.assertFalse(self.controller.dlc_items["EP01"].selected)
        self.assertFalse(self.controller.dlc_items["GP02"].selected)

        # Filter by installed
        catalog_view._set_filter("INSTALLED")
        self.assertEqual(catalog_view.active_filter, "INSTALLED")

        window.quit()

    def test_dialogs_instantiation(self):
        """Verify that ConfirmationDialog, ErrorDialog, ConsentDialog, DryRunDialog instantiate without errors."""
        window = MainWindow(self.controller)

        # 1. Confirmation dialog
        confirmed = []
        dlg_confirm = ConfirmationDialog(
            window,
            title="Test Confirmation",
            message="Do you confirm?",
            theme=self.branding.theme,
            on_confirm=lambda: confirmed.append(True)
        )
        dlg_confirm._on_confirm_action()
        self.assertEqual(confirmed, [True])

        # 2. Error dialog
        dlg_err = ErrorDialog(
            window,
            title="Test Error",
            error_message="Something failed",
            theme=self.branding.theme
        )
        dlg_err.destroy()

        # 3. User Consent Gate dialog
        agreed = []
        dlg_consent = ConsentDialog(
            window,
            on_agree=lambda: agreed.append(True)
        )
        dlg_consent._on_agree_action()
        self.assertEqual(agreed, [True])

        # 4. Dry Run dialog (with dict)
        fake_plan = {
            "create_files": ["Game/EP01/Data/test.package"],
            "overwrite_files": ["Game/EP01/Client.package"],
            "required_backup_size_bytes": 1024 * 1024 * 10
        }
        dlg_dry = DryRunDialog(
            window,
            item_id="EP01",
            plan=fake_plan,
            theme=self.branding.theme
        )
        dlg_dry.destroy()

        # 5. Dry Run dialog (with real DryRunReport dataclass)
        from core.installer import DryRunReport
        real_report = DryRunReport(
            dlc_code="EP01",
            archive_path="/tmp/fake.zip",
            total_files=2,
            creates_count=1,
            overwrites_count=1,
            total_incoming_bytes=2048,
            total_backup_bytes_needed=1024 * 1024 * 5,
            planned_creates=["Game/EP01/New.package"],
            planned_overwrites=[("Game/EP01/Old.package", 1024 * 1024 * 5)]
        )
        dlg_dry_real = DryRunDialog(
            window,
            item_id="EP01",
            plan=real_report,
            theme=self.branding.theme
        )
        dlg_dry_real.destroy()

        # 6. BackupsView with real UserBackupMetadata objects
        from core.backup_manager import UserBackupMetadata
        mock_bm = unittest.mock.MagicMock()
        mock_bm.list_user_backups.return_value = [
            UserBackupMetadata(
                backup_id="ubak_saves_1",
                store_id="store_alpha",
                backup_type="SAVES",
                archive_filename="user_backup_saves_20261105.zip",
                archive_path="/tmp/user_backup_saves_20261105.zip",
                created_at="2026-11-05T08:00:00",
                size_bytes=1024 * 500,
                file_count=12,
                source_dir="/tmp/saves"
            )
        ]
        self.controller.set_backup_manager(mock_bm)
        window.view_backups.update_view()

        window.quit()

    def test_sims4_manager_app_bootstrap(self):
        """Test full application bootstrap with store_alpha and store_beta profiles."""
        app_alpha = Sims4ManagerApp(
            store_id="store_alpha",
            base_appdata_dir=str(self.base_path)
        )
        self.assertEqual(app_alpha.context.store_id, "store_alpha")
        self.assertEqual(app_alpha.branding.theme.theme_id, "sims4_dark_green")
        app_alpha.window.quit()

        app_beta = Sims4ManagerApp(
            store_id="store_beta",
            base_appdata_dir=str(self.base_path)
        )
        self.assertEqual(app_beta.context.store_id, "store_beta")
        self.assertEqual(app_beta.branding.theme.theme_id, "modern_cyber_blue")
        app_beta.window.quit()

    def test_state_database_get_transaction_history_and_history_view(self):
        """Verify StateDatabase.get_transaction_history API and HistoryView integration."""
        from database.database import StateDatabase
        from database.models import TransactionStatus, FileActionType, FileLogStatus
        import time

        db = StateDatabase(self.context.state_db_path)
        # Empty DB check
        self.assertEqual(db.get_transaction_history(), [])

        # Create a real transaction and file log
        tx = db.create_transaction(
            tx_id="tx_test_audit_01",
            store_id="store_alpha",
            dlc_code="EP01",
            lease_token="00000000-0000-4000-8000-000000000001",
            owner_pid=os.getpid(),
            initial_status=TransactionStatus.INSTALLED
        )
        # Verify TransactionRecord supports dictionary-style indexing
        self.assertEqual(tx["tx_id"], "tx_test_audit_01")
        self.assertEqual(tx["dlc_code"], "EP01")

        db.log_file_action(
            tx_id="tx_test_audit_01",
            relative_path="Game/EP01/Client.package",
            action_type=FileActionType.CREATE,
            staging_path="/tmp/staging.pkg",
            target_sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            status=FileLogStatus.APPLIED
        )

        history = db.get_transaction_history(limit=10)
        self.assertEqual(len(history), 1)
        item = history[0]
        self.assertEqual(item["tx_id"], "tx_test_audit_01")
        self.assertEqual(item["dlc_code"], "EP01")
        self.assertEqual(item["operation_type"], "INSTALL")
        self.assertEqual(item["files_count"], 1)

        # Wire with controller and verify HistoryView
        self.controller.set_state_db(db)
        window = MainWindow(self.controller)
        window.view_history.update_view()
        # Verify that children widgets were created in log_scroll
        children = window.view_history.log_scroll.winfo_children()
        self.assertGreater(len(children), 0)
        window.quit()

    def test_game_finder_methods_and_controller_scan(self):
        """Verify GameFinder API aliases and AppController.scan_game_installation execution."""
        from core.game_finder import GameFinder
        import time

        finder = GameFinder()
        # Verify required API methods are all present
        self.assertTrue(hasattr(finder, "find_game"))
        self.assertTrue(hasattr(finder, "find_game_directory"))
        self.assertTrue(hasattr(finder, "validate_custom_path"))
        self.assertTrue(hasattr(finder, "validate_game_directory"))
        self.assertTrue(hasattr(finder, "is_ntfs_filesystem"))
        self.assertTrue(hasattr(finder, "deep_scan_dlcs"))

        # Verify is_ntfs_filesystem returns bool without raising
        self.assertIsInstance(finder.is_ntfs_filesystem(str(self.base_path)), bool)

        # Create mock valid Sims 4 directory with EP01 DLC
        game_dir = self.base_path / "The Sims 4"
        (game_dir / "Game" / "Bin").mkdir(parents=True)
        (game_dir / "Game" / "Bin" / "TS4_x64.exe").write_bytes(b"MZ_DUMMY_EXE")
        (game_dir / "EP01").mkdir()
        (game_dir / "EP01" / "EP01.package").write_bytes(b"PACKAGE_BYTES")

        self.controller.dlc_items["EP01"] = DLCUIItem(
            item_id="EP01",
            title="EP01 Get to Work",
            dlc_type="EP",
            archive_name="EP01.zip",
            archive_size=1024,
            status=DLCStatus.AVAILABLE
        )

        # Trigger scan
        self.controller.scan_game_installation(custom_path=str(game_dir))

        # Wait for worker thread to complete
        for _ in range(50):
            if not self.controller.operation_state.is_busy:
                break
            time.sleep(0.05)

        self.assertFalse(self.controller.operation_state.is_busy)
        self.assertTrue(self.controller.game_info.found)
        self.assertEqual(self.controller.game_info.installed_dlc_count, 1)
        self.assertEqual(self.controller.dlc_items["EP01"].status, DLCStatus.INSTALLED)
        self.assertEqual(self.controller.game_info.error_message, "")

    def test_controller_yandex_client_getter(self):
        """Verify AppController.get_yandex_client correctly imports and instantiates YandexClient."""
        from core.yandex_client import YandexClient
        client = self.controller.get_yandex_client()
        self.assertIsInstance(client, YandexClient)


if __name__ == "__main__":
    unittest.main()

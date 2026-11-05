"""
Unit tests for AppContext store data isolation and profile loading.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 5.1 and 5.2.
"""

import os
from pathlib import Path
import unittest

from core.app_context import AppContext, AppContextError
from tests.conftest import IsolatedEnvironmentTestCase


class TestAppContext(IsolatedEnvironmentTestCase):

    def test_store_isolation_paths(self):
        """
        Verify that different store_id instances have strictly separate
        storage directories under the simulated %LOCALAPPDATA% root.
        """
        ctx_alpha = AppContext(
            store_id="store_alpha",
            store_name="Store Alpha",
            application_name="Sims 4 DLC Loader - Alpha",
            base_appdata_dir=self.appdata_dir
        )
        ctx_beta = AppContext(
            store_id="store_beta",
            store_name="Store Beta",
            application_name="Sims 4 DLC Loader - Beta",
            base_appdata_dir=self.appdata_dir
        )

        # Base directories must differ
        self.assertNotEqual(ctx_alpha.store_dir, ctx_beta.store_dir)
        self.assertTrue(str(ctx_alpha.store_dir).endswith("store_alpha"))
        self.assertTrue(str(ctx_beta.store_dir).endswith("store_beta"))

        # Database paths must be distinct
        self.assertNotEqual(ctx_alpha.state_db_path, ctx_beta.state_db_path)
        self.assertNotEqual(ctx_alpha.catalog_db_path, ctx_beta.catalog_db_path)

        # Locks and downloads must be distinct
        self.assertNotEqual(ctx_alpha.locks_dir, ctx_beta.locks_dir)
        self.assertNotEqual(ctx_alpha.downloads_dir, ctx_beta.downloads_dir)
        self.assertNotEqual(ctx_alpha.user_backups_dir, ctx_beta.user_backups_dir)
        self.assertNotEqual(ctx_alpha.logs_dir, ctx_beta.logs_dir)

    def test_ensure_directories(self):
        """Verify that ensure_directories() creates all required folders."""
        ctx = AppContext(
            store_id="store_alpha",
            store_name="Store Alpha",
            application_name="Sims 4 DLC Loader - Alpha",
            base_appdata_dir=self.appdata_dir
        )

        # Before ensure_directories
        self.assertFalse(ctx.database_dir.exists())
        self.assertFalse(ctx.locks_dir.exists())

        ctx.ensure_directories()

        self.assertTrue(ctx.database_dir.is_dir())
        self.assertTrue(ctx.downloads_dir.is_dir())
        self.assertTrue(ctx.user_backups_dir.is_dir())
        self.assertTrue(ctx.locks_dir.is_dir())
        self.assertTrue(ctx.logs_dir.is_dir())
        self.assertTrue(ctx.cache_dir.is_dir())

    def test_from_store_json(self):
        """Verify loading store configuration from store.json."""
        project_root = Path(__file__).resolve().parent.parent
        store_alpha_json = project_root / "configs" / "stores" / "store_alpha" / "store.json"

        ctx = AppContext.from_store_json(str(store_alpha_json), base_appdata_dir=self.appdata_dir)
        self.assertEqual(ctx.store_id, "store_alpha")
        self.assertEqual(ctx.store_name, "Fanfiriel Games Store")
        self.assertEqual(ctx.theme_id, "sims4_dark_green")
        self.assertTrue(ctx.is_feature_enabled("dry_run_available"))
        self.assertTrue(ctx.is_feature_enabled("allow_custom_oauth"))

    def test_invalid_store_json_raises(self):
        """Verify that missing store_id or missing file raises AppContextError."""
        with self.assertRaises(AppContextError):
            AppContext.from_store_json("/non/existent/path/store.json")

        with self.assertRaises(AppContextError):
            AppContext(store_id="")

    def test_store_id_path_traversal_blocked(self):
        """Verify that invalid characters or path traversal in store_id are strictly rejected."""
        invalid_ids = ["../../traversal", "store/slash", "store\\backslash", "store alpha", "store@special!"]
        for bad_id in invalid_ids:
            with self.subTest(bad_id=bad_id):
                with self.assertRaises(AppContextError):
                    AppContext(store_id=bad_id)

    def test_sources_validation(self):
        """Verify that sources dictionary must contain primary_dlc_folder."""
        with self.assertRaises(AppContextError):
            AppContext(store_id="store_test", sources={"invalid_key": "http://example.com"})

    def test_singleton_context_management(self):
        """Verify set_current_context and get_current_context lifecycle."""
        from core.app_context import set_current_context, get_current_context
        ctx = AppContext(store_id="store_singleton", base_appdata_dir=self.appdata_dir)
        set_current_context(ctx)
        self.assertEqual(get_current_context().store_id, "store_singleton")


if __name__ == "__main__":
    unittest.main()

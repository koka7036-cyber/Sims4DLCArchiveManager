"""
Tests for Post Install Helper («DLC Помощник») and User Consent Gate (Stage 7).
Strictly conforms to PROJECT_SPEC.md v1.1.5 Section 10.
"""

import os
import sys
import zipfile
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.app_context import AppContext, set_current_context
from core.post_install_helper import (
    PostInstallHelperManager,
    HelperConsentRequest,
    HelperLaunchResult,
    HelperLaunchStatus,
)


class TestPostInstallHelper(unittest.TestCase):
    """Unit tests for Post Install Helper utility and User Consent Gate."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="test_helper_")
        self.appdata_dir = os.path.join(self.temp_dir, "LocalAppData")
        self.game_root = os.path.join(self.temp_dir, "The Sims 4")
        os.makedirs(self.appdata_dir, exist_ok=True)
        os.makedirs(self.game_root, exist_ok=True)

        self.ctx = AppContext(
            store_id="store_test_helper",
            base_appdata_dir=self.appdata_dir,
            sources={
                "primary_dlc_folder": "https://disk.yandex.ru/d/example_dlc",
                "helper_archive_url": "https://disk.yandex.ru/d/example_helper"
            }
        )
        self.ctx.ensure_directories()
        set_current_context(self.ctx)

        self.mgr = PostInstallHelperManager(self.ctx)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_mock_helper_archive(self, archive_path: str, include_prohibited_ts4: bool = False) -> None:
        """Helper to create a zip containing helper binaries."""
        os.makedirs(os.path.dirname(os.path.abspath(archive_path)), exist_ok=True)
        with zipfile.ZipFile(archive_path, "w") as zf:
            # Main helper binary (script/batch executable)
            zf.writestr("dlc_helper.bat", b"@echo off\r\necho Helper running...\r\n")
            zf.writestr("helper_config.ini", b"[Config]\r\nAuto=1\r\n")
            if include_prohibited_ts4:
                zf.writestr("TS4_x64.exe", b"FAKE_GAME_EXE")

    def test_user_consent_gate_declined(self):
        """
        Verify that if the user declines execution in the User Consent Gate,
        the process is NOT executed and returns DECLINED.
        """
        archive_path = os.path.join(self.ctx.downloads_dir, "dlc_helper.zip")
        self._create_mock_helper_archive(archive_path)

        consent_called = False
        def mock_consent_decline(req: HelperConsentRequest) -> bool:
            nonlocal consent_called
            consent_called = True
            self.assertEqual(req.executable_name, "dlc_helper.bat")
            self.assertEqual(req.game_root, os.path.abspath(self.game_root))
            return False  # User clicks NO

        result = self.mgr.prepare_and_launch(
            game_root=self.game_root,
            consent_callback=mock_consent_decline
        )

        self.assertTrue(consent_called, "Consent callback must be triggered")
        self.assertEqual(result.status, HelperLaunchStatus.DECLINED)
        self.assertIsNone(result.pid)

    @patch("subprocess.Popen")
    def test_user_consent_gate_accepted_runs_with_cwd(self, mock_popen):
        """
        Verify that when user approves in the User Consent Gate,
        subprocess.Popen is called with cwd = game_root.
        """
        mock_proc = MagicMock()
        mock_proc.pid = 9988
        mock_popen.return_value = mock_proc

        archive_path = os.path.join(self.ctx.downloads_dir, "dlc_helper.zip")
        self._create_mock_helper_archive(archive_path)

        consent_called = False
        def mock_consent_accept(req: HelperConsentRequest) -> bool:
            nonlocal consent_called
            consent_called = True
            self.assertIn("dlc_helper.bat", req.executable_name)
            return True  # User clicks YES

        result = self.mgr.prepare_and_launch(
            game_root=self.game_root,
            consent_callback=mock_consent_accept
        )

        self.assertTrue(consent_called)
        self.assertEqual(result.status, HelperLaunchStatus.SUCCESS)
        self.assertEqual(result.pid, 9988)

        # Verify subprocess.Popen args
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        self.assertEqual(kwargs.get("cwd"), os.path.abspath(self.game_root), "cwd must strictly be game_root")

    def test_prohibited_game_exe_not_selected_as_helper(self):
        """
        Verify that TS4_x64.exe is never selected as the helper utility executable.
        """
        archive_path = os.path.join(self.ctx.downloads_dir, "dlc_helper.zip")
        self._create_mock_helper_archive(archive_path, include_prohibited_ts4=True)

        selected_exe_name = ""
        def mock_consent(req: HelperConsentRequest) -> bool:
            nonlocal selected_exe_name
            selected_exe_name = req.executable_name
            return False

        _ = self.mgr.prepare_and_launch(
            game_root=self.game_root,
            consent_callback=mock_consent
        )

        self.assertEqual(selected_exe_name, "dlc_helper.bat", "TS4_x64.exe must not be chosen as helper")


if __name__ == "__main__":
    unittest.main()

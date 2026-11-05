"""
Post Install Helper module («DLC Помощник»).
Strictly conforms to PROJECT_SPEC.md v1.1.5 Section 10 and Stage 7 requirements.

Implements:
1. Source URL resolution strictly from AppContext store profile.
2. Zero Blind Trust: verification of archive hash.
3. Safe archive inspection with whitelist for helper binaries (is_helper_archive=True).
4. Mandatory User Consent Gate: explicit user approval required before any execution.
5. Process execution strictly with cwd = game_root.
6. Safe handling of cancellation, declining, and execution errors.
"""

from __future__ import annotations

import os
import sys
import shutil
import subprocess
import hashlib
from pathlib import Path
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable, Dict, Any, List

from core.app_context import AppContext, get_current_context
from core.archive_manager import ArchiveManager
from core.archive_inspector import DLCArchiveInspector
from core.downloader import FileDownloader, DownloadProgress
from core.exceptions import (
    Sims4ManagerError,
    ArchiveError,
    CorruptedArchiveError,
    BlockedBinaryError,
    HashMismatchError,
    NetworkError,
)
from core.logger import get_logger
from core.yandex_client import YandexClient
from database.recovery import compute_sha256

logger = get_logger("core.post_install_helper")


class HelperLaunchStatus(str, Enum):
    SUCCESS = "SUCCESS"
    DECLINED = "DECLINED"
    CANCELLED = "CANCELLED"
    ERROR = "ERROR"


@dataclass
class HelperConsentRequest:
    """Diagnostic details presented to user in User Consent Gate prior to launch."""
    executable_name: str
    relative_path: str
    absolute_path: str
    file_size_bytes: int
    sha256: str
    game_root: str


@dataclass
class HelperLaunchResult:
    """Outcome of helper tool preparation and execution."""
    status: HelperLaunchStatus
    executable_path: Optional[str] = None
    pid: Optional[int] = None
    error_message: Optional[str] = None


class PostInstallHelperManager:
    """
    Manages downloading, verifying, inspecting, and launching the trusted DLC Helper utility.
    """

    def __init__(self, app_context: Optional[AppContext] = None):
        self.ctx = app_context or get_current_context()

    def get_helper_source_url(self) -> str:
        """Resolves helper archive URL from store profile."""
        url = self.ctx.sources.get("helper_archive_url")
        if not url:
            raise Sims4ManagerError("Store profile does not configure 'helper_archive_url'.")
        return url

    def prepare_and_launch(
        self,
        game_root: str,
        consent_callback: Callable[[HelperConsentRequest], bool],
        expected_sha256: Optional[str] = None,
        downloader: Optional[FileDownloader] = None,
        download_progress_callback: Optional[Callable[[DownloadProgress], None]] = None,
        cancel_event: Optional[Any] = None
    ) -> HelperLaunchResult:
        """
        Full workflow for DLC Helper:
        1. Download archive from store profile source URL.
        2. Verify archive hash.
        3. Inspect archive with is_helper_archive=True (allows approved binaries).
        4. Extract into controlled helper directory inside game root or staging.
        5. Locate main utility executable (excluding TS4_x64.exe, TS4.exe).
        6. Present User Consent Gate: execute only if consent_callback returns True.
        7. Launch process with cwd = game_root.
        """
        norm_game_root = os.path.abspath(game_root)
        if not os.path.isdir(norm_game_root):
            return HelperLaunchResult(
                status=HelperLaunchStatus.ERROR,
                error_message=f"Sims 4 game root directory not found: '{norm_game_root}'"
            )

        helper_url = self.get_helper_source_url()
        download_dir = str(self.ctx.downloads_dir)
        os.makedirs(download_dir, exist_ok=True)
        archive_dest = os.path.join(download_dir, "dlc_helper.zip")

        # 1. Download if not already present
        if not os.path.exists(archive_dest):
            logger.info(f"Downloading helper archive from: {helper_url}")
            # If Yandex URL, resolve direct link
            direct_url = helper_url
            if "disk.yandex" in helper_url or "yadi.sk" in helper_url:
                try:
                    client = YandexClient()
                    direct_url = client.get_public_download_url(helper_url)
                except Exception as e:
                    logger.error(f"Failed to resolve Yandex download URL for helper: {e}")
                    return HelperLaunchResult(
                        status=HelperLaunchStatus.ERROR,
                        error_message=f"Failed to resolve helper download URL: {e}"
                    )

            dl = downloader or FileDownloader(max_retries=3)
            try:
                dl.download_file(
                    url=direct_url,
                    destination_path=archive_dest,
                    expected_sha256=expected_sha256,
                    progress_callback=download_progress_callback,
                    cancel_event=cancel_event
                )
            except Exception as dl_err:
                logger.error(f"Failed to download helper archive: {dl_err}")
                return HelperLaunchResult(
                    status=HelperLaunchStatus.ERROR,
                    error_message=f"Download failed: {dl_err}"
                )

        # 2. Check hash if expected
        actual_sha = compute_sha256(archive_dest)
        if expected_sha256 and actual_sha.lower() != expected_sha256.lower():
            return HelperLaunchResult(
                status=HelperLaunchStatus.ERROR,
                error_message=f"Helper archive hash mismatch: expected {expected_sha256}, got {actual_sha}"
            )

        # 3. Inspect archive with is_helper_archive=True
        inspector = DLCArchiveInspector()
        report = inspector.inspect_archive(archive_dest, is_helper_archive=True, target_dir=norm_game_root)
        if not report.is_valid:
            return HelperLaunchResult(
                status=HelperLaunchStatus.ERROR,
                error_message=f"Helper archive failed inspection: {report.error_message}"
            )

        # 4. Extract into helper folder inside Game root or isolated destination
        target_helper_dir = os.path.join(norm_game_root, "__helper")
        os.makedirs(target_helper_dir, exist_ok=True)

        extracted_files: List[str] = []
        with ArchiveManager.open_archive(archive_dest) as reader:
            for entry in report.entries:
                if entry.is_dir:
                    continue
                rel_path = report.safe_relative_paths.get(entry.path, entry.path)
                out_path = os.path.join(target_helper_dir, rel_path)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)
                with reader.open_member(entry.path) as src, open(out_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                extracted_files.append(out_path)

        # 5. Locate helper executable
        helper_exe = self._find_helper_executable(extracted_files)
        if not helper_exe:
            return HelperLaunchResult(
                status=HelperLaunchStatus.ERROR,
                error_message="Could not locate valid executable in helper archive."
            )

        exe_sha = compute_sha256(helper_exe)
        exe_size = os.path.getsize(helper_exe)

        # 6. User Consent Gate
        consent_req = HelperConsentRequest(
            executable_name=os.path.basename(helper_exe),
            relative_path=os.path.relpath(helper_exe, norm_game_root),
            absolute_path=helper_exe,
            file_size_bytes=exe_size,
            sha256=exe_sha,
            game_root=norm_game_root
        )

        user_approved = consent_callback(consent_req)
        if not user_approved:
            logger.info("User declined execution of helper utility in User Consent Gate.")
            return HelperLaunchResult(
                status=HelperLaunchStatus.DECLINED,
                executable_path=helper_exe,
                error_message="User declined launch authorization."
            )

        # 7. Launch process with cwd = game_root
        try:
            logger.info(f"Launching helper process: '{helper_exe}' with cwd='{norm_game_root}'")
            # In test/sandbox environments, launch mock or standard process
            proc = subprocess.Popen(
                [helper_exe],
                cwd=norm_game_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            return HelperLaunchResult(
                status=HelperLaunchStatus.SUCCESS,
                executable_path=helper_exe,
                pid=proc.pid
            )
        except OSError as e:
            logger.error(f"Failed to launch helper executable: {e}")
            return HelperLaunchResult(
                status=HelperLaunchStatus.ERROR,
                executable_path=helper_exe,
                error_message=f"Process launch failed: {e}"
            )

    @staticmethod
    def _find_helper_executable(extracted_files: List[str]) -> Optional[str]:
        """
        Finds the utility executable in the extracted files list,
        explicitly excluding Sims 4 game binaries.
        """
        prohibited_names = {"ts4_x64.exe", "ts4.exe", "ts4_dx9_x64.exe", "activationui.exe"}
        for f in extracted_files:
            bname = os.path.basename(f).lower()
            if bname in prohibited_names:
                continue
            if bname.endswith(".exe") or bname.endswith(".bat") or bname.endswith(".cmd"):
                return f
        return None

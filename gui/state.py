"""
UI State models and status enumerations.
Provides typed, presentation-ready structures representing the game, DLC items,
downloads, and operational status without containing direct business logic.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Dict, Any


class DLCStatus(str, Enum):
    AVAILABLE = "AVAILABLE"            # Not installed, not downloaded
    DOWNLOADING = "DOWNLOADING"        # Range 206 stream active
    DOWNLOADED = "DOWNLOADED"          # Archive ready in downloads
    VERIFYING = "VERIFYING"            # Archive structure / hash verification
    READY_TO_INSTALL = "READY_TO_INSTALL"  # Verified, ready for two-phase install
    INSTALLED = "INSTALLED"            # Valid .package found in game directory
    ERROR = "ERROR"                    # Operation failed


class TaskState(str, Enum):
    IDLE = "IDLE"
    SCANNING_GAME = "SCANNING_GAME"
    LOADING_CATALOG = "LOADING_CATALOG"
    DOWNLOADING = "DOWNLOADING"
    VERIFYING_ARCHIVE = "VERIFYING_ARCHIVE"
    SIMULATING_DRY_RUN = "SIMULATING_DRY_RUN"
    INSTALLING = "INSTALLING"
    CREATING_BACKUP = "CREATING_BACKUP"
    RESTORING_BACKUP = "RESTORING_BACKUP"
    RUNNING_HELPER = "RUNNING_HELPER"
    CRASH_RECOVERY = "CRASH_RECOVERY"


@dataclass
class DLCUIItem:
    item_id: str
    title: str
    dlc_type: str = "EP"               # EP, GP, SP, FP
    archive_name: str = ""
    archive_size: int = 0
    unpacked_size: int = 0
    status: DLCStatus = DLCStatus.AVAILABLE
    download_progress: float = 0.0     # 0.0 to 1.0
    download_speed: float = 0.0        # Bytes / second
    eta_seconds: int = 0
    error_message: str = ""
    selected: bool = False
    public_url: str = ""
    sha256: str = ""
    md5: str = ""

    @property
    def formatted_archive_size(self) -> str:
        if self.archive_size <= 0:
            return "—"
        mb = self.archive_size / (1024 * 1024)
        if mb >= 1024:
            return f"{mb / 1024:.2f} GB"
        return f"{mb:.1f} MB"

    @property
    def formatted_unpacked_size(self) -> str:
        if self.unpacked_size <= 0:
            return "—"
        mb = self.unpacked_size / (1024 * 1024)
        if mb >= 1024:
            return f"{mb / 1024:.2f} GB"
        return f"{mb:.1f} MB"

    @property
    def formatted_speed(self) -> str:
        if self.download_speed <= 0:
            return "—"
        mb = self.download_speed / (1024 * 1024)
        return f"{mb:.2f} MB/s"

    @property
    def formatted_eta(self) -> str:
        if self.eta_seconds <= 0:
            return "—"
        mins, secs = divmod(self.eta_seconds, 60)
        if mins > 0:
            return f"{mins}м {secs}с"
        return f"{secs}с"


@dataclass
class GameUIInfo:
    found: bool = False
    path: str = ""
    version: str = "Unknown"
    filesystem: str = "NTFS"
    volume_name: str = ""
    free_space_bytes: int = 0
    installed_dlc_count: int = 0
    error_message: str = ""

    @property
    def formatted_free_space(self) -> str:
        if self.free_space_bytes <= 0:
            return "—"
        gb = self.free_space_bytes / (1024 * 1024 * 1024)
        return f"{gb:.1f} GB"


@dataclass
class OperationState:
    task: TaskState = TaskState.IDLE
    is_busy: bool = False
    status_message: str = "Готов к работе"
    progress: float = 0.0
    active_tx_id: Optional[str] = None
    last_error: Optional[str] = None

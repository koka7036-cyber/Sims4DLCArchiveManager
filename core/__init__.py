"""Core package for Sims 4 DLC Archive & Install Manager."""

from core.nt_handle import (
    SafeNTHandle,
    open_directory_handle,
    step_traverse_directories,
    move_file_replace_if_exists_false,
)
from core.integrity import (
    verify_triple_identity,
    assert_verified_object,
    detect_hardlink_anomalies,
)
from core.backup_manager import (
    BackupManager,
    UserBackupMetadata,
)
from core.installer import (
    DLCInstaller,
    DryRunReport,
)
from core.post_install_helper import (
    PostInstallHelperManager,
    HelperConsentRequest,
    HelperLaunchResult,
    HelperLaunchStatus,
)

__all__ = [
    "SafeNTHandle",
    "open_directory_handle",
    "step_traverse_directories",
    "move_file_replace_if_exists_false",
    "verify_triple_identity",
    "assert_verified_object",
    "detect_hardlink_anomalies",
    "BackupManager",
    "UserBackupMetadata",
    "DLCInstaller",
    "DryRunReport",
    "PostInstallHelperManager",
    "HelperConsentRequest",
    "HelperLaunchResult",
    "HelperLaunchStatus",
]

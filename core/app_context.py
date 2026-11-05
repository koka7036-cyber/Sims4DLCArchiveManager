"""
Store Context and White Label isolation engine.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 5.1 and 5.2.
"""

import os
import sys
import json
from pathlib import Path
from typing import Dict, Any, Optional

from core.exceptions import Sims4ManagerError
from core.logger import get_logger

logger = get_logger("app_context")


class AppContextError(Sims4ManagerError):
    """Raised when AppContext initialization or store configuration fails."""
    pass


class AppContext:
    """
    Encapsulates Store profile configuration and enforces strict physical isolation
    of user data, databases, locks, downloads, backups, and logs under %LOCALAPPDATA%.
    """

    def __init__(
        self,
        store_id: str,
        store_name: Optional[str] = None,
        application_name: Optional[str] = None,
        application_version: str = "1.0.0",
        theme_id: str = "sims4_dark_green",
        sources: Optional[Dict[str, str]] = None,
        features: Optional[Dict[str, bool]] = None,
        base_appdata_dir: Optional[str] = None
    ):
        if not store_id or not isinstance(store_id, str):
            raise AppContextError("store_id must be a non-empty string.")

        import re
        if not re.match(r'^[a-zA-Z0-9_-]+$', store_id):
            raise AppContextError(
                f"Invalid store_id '{store_id}': must contain only alphanumeric characters, underscores, and hyphens."
            )

        self.store_id = store_id
        self.store_name = store_name or store_id
        self.application_name = application_name or f"Sims 4 DLC Archive Manager - {self.store_name}"
        self.application_version = application_version
        self.theme_id = theme_id
        self.sources = sources or {
            "primary_dlc_folder": "https://disk.yandex.ru/d/pYDq0CSIwqclFA",
            "helper_archive_url": "https://disk.yandex.ru/d/RgVixKr-6QlLsQ"
        }
        if not isinstance(self.sources, dict) or "primary_dlc_folder" not in self.sources:
            raise AppContextError("AppContext sources dictionary must contain 'primary_dlc_folder'.")

        self.features = features or {
            "allow_custom_oauth": True,
            "allow_backup_management": True,
            "allow_verify_only": True,
            "auto_run_helper_default": True,
            "dry_run_available": True
        }

        # Determine root data storage path
        if base_appdata_dir:
            self._root_appdata = Path(base_appdata_dir).resolve()
        else:
            if sys.platform == "win32":
                local_app_data = os.environ.get("LOCALAPPDATA")
                if not local_app_data:
                    local_app_data = str(Path.home() / "AppData" / "Local")
            else:
                # POSIX fallback
                local_app_data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
            self._root_appdata = (Path(local_app_data) / "Sims4DLCArchiveManager").resolve()

        # Dedicated isolated store root
        self.store_dir = self._root_appdata / self.store_id

        # Isolated subdirectories per PROJECT_SPEC.md 5.2
        self.database_dir = self.store_dir / "database"
        self.downloads_dir = self.store_dir / "downloads"
        self.user_backups_dir = self.store_dir / "user_backups"
        self.locks_dir = self.store_dir / "locks"
        self.logs_dir = self.store_dir / "logs"
        self.cache_dir = self.store_dir / "cache"

    def ensure_directories(self) -> None:
        """
        Creates all required isolated directories on disk with parent directories.
        """
        for d in (
            self.database_dir,
            self.downloads_dir,
            self.user_backups_dir,
            self.locks_dir,
            self.logs_dir,
            self.cache_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def state_db_path(self) -> Path:
        """Path to critical FSM state database (SQLite synchronous=FULL)."""
        return self.database_dir / "state.db"

    @property
    def catalog_db_path(self) -> Path:
        """Path to catalog & cloud cache database (SQLite synchronous=NORMAL)."""
        return self.database_dir / "catalog.db"

    @property
    def log_file_path(self) -> Path:
        """Path to rotating application log file."""
        return self.logs_dir / "app.log"

    def get_lock_file_path(self, tx_id: str) -> Path:
        """Path to exclusive transaction lock file."""
        return self.locks_dir / f"tx_{tx_id}.lock"

    def get_uac_resume_file_path(self, tx_id: str) -> Path:
        """Path to UAC resume secret file."""
        return self.locks_dir / f"uac_resume_{tx_id}.dat"

    def is_feature_enabled(self, feature_name: str) -> bool:
        """Returns True if the specified feature is enabled in store profile."""
        return bool(self.features.get(feature_name, False))

    @classmethod
    def from_store_json(
        cls,
        store_json_path: str,
        base_appdata_dir: Optional[str] = None
    ) -> "AppContext":
        """
        Loads store profile configuration from store.json and instantiates AppContext.
        """
        path = Path(store_json_path)
        if not path.is_file():
            raise AppContextError(f"Store configuration file not found: {path}")

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            raise AppContextError(f"Failed to parse store.json: {e}") from e

        store_id = data.get("store_id")
        if not store_id:
            raise AppContextError("store.json missing required field 'store_id'.")

        return cls(
            store_id=store_id,
            store_name=data.get("store_name", store_id),
            application_name=data.get("application_name", f"Sims 4 DLC Loader - {store_id}"),
            application_version=data.get("application_version", "1.0.0"),
            theme_id=data.get("theme_id", "sims4_dark_green"),
            sources=data.get("sources"),
            features=data.get("features"),
            base_appdata_dir=base_appdata_dir
        )


# Singleton context management
_CURRENT_CONTEXT: Optional[AppContext] = None


def get_current_context() -> AppContext:
    """Returns current active AppContext or raises error if uninitialized."""
    global _CURRENT_CONTEXT
    if _CURRENT_CONTEXT is None:
        raise AppContextError("AppContext has not been initialized.")
    return _CURRENT_CONTEXT


def set_current_context(context: AppContext) -> None:
    """Sets active singleton AppContext."""
    global _CURRENT_CONTEXT
    if not isinstance(context, AppContext):
        raise AppContextError("Provided context is not an instance of AppContext.")
    _CURRENT_CONTEXT = context

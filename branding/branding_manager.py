"""
Branding Manager for White Label presentation layer.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 5.1 and 11.
Provides single access point for active store identity, colors, and assets.
"""

from pathlib import Path
from typing import Optional, Dict

from core.app_context import AppContext, get_current_context
from core.logger import get_logger
from branding.theme_manager import ThemeManager
from branding.asset_manager import AssetManager

logger = get_logger("branding_manager")


class BrandingManager:
    """
    Coordinates store identity, theme styling, and visual assets for the GUI.
    Strictly decouples all visual branding from hardcoded store names and colors.
    """

    def __init__(
        self,
        context: Optional[AppContext] = None,
        config_dir: Optional[Path] = None
    ):
        self.context = context or get_current_context()
        self.config_dir = config_dir or Path("configs") / "stores" / self.context.store_id

        # Initialize ThemeManager from theme.json if present
        theme_path = self.config_dir / "theme.json"
        if theme_path.is_file():
            self.theme = ThemeManager.from_theme_json(str(theme_path))
        else:
            self.theme = ThemeManager(self.context.theme_id)

        # Initialize AssetManager
        self.assets = AssetManager(self.config_dir)

    @property
    def store_id(self) -> str:
        return self.context.store_id

    @property
    def store_name(self) -> str:
        return self.context.store_name

    @property
    def application_name(self) -> str:
        return self.context.application_name

    @property
    def application_version(self) -> str:
        return self.context.application_version

    @property
    def primary_dlc_folder(self) -> str:
        return self.context.sources.get("primary_dlc_folder", "")

    @property
    def helper_archive_url(self) -> str:
        return self.context.sources.get("helper_archive_url", "")

    def is_feature_enabled(self, feature_name: str) -> bool:
        return self.context.is_feature_enabled(feature_name)

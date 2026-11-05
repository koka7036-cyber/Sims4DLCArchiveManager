"""
Asset Manager for White Label graphic resources.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 5.1 and 11.
Manages logos, window icons, banners, and fallbacks.
"""

from pathlib import Path
from typing import Optional, Dict, Any

from core.logger import get_logger

logger = get_logger("asset_manager")


class AssetManager:
    """
    Manages loading, validating, and retrieving graphic assets for a specific store.
    Provides graceful fallbacks if image files are missing or unreadable.
    """

    def __init__(self, store_dir: Optional[Path] = None):
        self.store_dir = store_dir
        self._cache: Dict[str, Any] = {}

    def get_logo_path(self) -> Optional[Path]:
        """Returns path to store logo (logo.png) if present."""
        if not self.store_dir:
            return None
        candidates = [
            self.store_dir / "assets" / "logo.png",
            self.store_dir / "logo.png",
        ]
        for p in candidates:
            if p.is_file():
                return p
        return None

    def get_icon_path(self) -> Optional[Path]:
        """Returns path to store window icon (icon.ico or icon.png) if present."""
        if not self.store_dir:
            return None
        candidates = [
            self.store_dir / "assets" / "icon.ico",
            self.store_dir / "assets" / "icon.png",
            self.store_dir / "icon.ico",
            self.store_dir / "icon.png",
        ]
        for p in candidates:
            if p.is_file():
                return p
        return None

    def get_banner_path(self) -> Optional[Path]:
        """Returns path to dashboard banner if present."""
        if not self.store_dir:
            return None
        candidates = [
            self.store_dir / "assets" / "banner.png",
            self.store_dir / "banner.png",
        ]
        for p in candidates:
            if p.is_file():
                return p
        return None

    def has_custom_assets(self) -> bool:
        """Checks if store directory contains any custom graphic assets."""
        return bool(self.get_logo_path() or self.get_icon_path() or self.get_banner_path())

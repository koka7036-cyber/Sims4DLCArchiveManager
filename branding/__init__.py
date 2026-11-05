"""
Branding and White Label presentation package.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 5.1 and 11.
"""

from branding.theme_manager import ThemeManager, BUILTIN_PALETTES
from branding.asset_manager import AssetManager
from branding.branding_manager import BrandingManager

__all__ = [
    "ThemeManager",
    "AssetManager",
    "BrandingManager",
    "BUILTIN_PALETTES",
]

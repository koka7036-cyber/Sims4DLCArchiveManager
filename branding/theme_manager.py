"""
Theme and Palette Manager for CustomTkinter GUI.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 5.1 and 11.
Supports pre-defined palettes:
- The Sims 4 Style (sims4_dark_green)
- Modern Gaming (modern_cyber_blue)
- Minimal Pro (minimal_pro_slate)
- Marketplace (marketplace_emerald)
- Custom Theme (custom_theme)
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional, Tuple

from core.logger import get_logger

logger = get_logger("theme_manager")

# Standard dark palettes defined in PROJECT_SPEC.md
BUILTIN_PALETTES: Dict[str, Dict[str, str]] = {
    "sims4_dark_green": {
        "background_dark": "#121815",
        "surface_dark": "#1a2420",
        "surface_secondary": "#24322c",
        "accent_primary": "#2ecc71",
        "accent_hover": "#27ae60",
        "accent_subtle": "#165b32",
        "text_primary": "#f0fdf4",
        "text_secondary": "#a7f3d0",
        "text_muted": "#6ee7b7",
        "border_color": "#2d4a3e",
        "danger": "#e74c3c",
        "warning": "#f39c12",
        "info": "#3498db",
    },
    "modern_cyber_blue": {
        "background_dark": "#0d1117",
        "surface_dark": "#161b22",
        "surface_secondary": "#21262d",
        "accent_primary": "#58a6ff",
        "accent_hover": "#1f6feb",
        "accent_subtle": "#113264",
        "text_primary": "#f0f6fc",
        "text_secondary": "#c9d1d9",
        "text_muted": "#8b949e",
        "border_color": "#30363d",
        "danger": "#f85149",
        "warning": "#d29922",
        "info": "#79c0ff",
    },
    "minimal_pro_slate": {
        "background_dark": "#0f172a",
        "surface_dark": "#1e293b",
        "surface_secondary": "#334155",
        "accent_primary": "#38bdf8",
        "accent_hover": "#0284c7",
        "accent_subtle": "#0369a1",
        "text_primary": "#f8fafc",
        "text_secondary": "#cbd5e1",
        "text_muted": "#94a3b8",
        "border_color": "#475569",
        "danger": "#ef4444",
        "warning": "#f59e0b",
        "info": "#0ea5e9",
    },
    "marketplace_emerald": {
        "background_dark": "#141e1b",
        "surface_dark": "#1c2b26",
        "surface_secondary": "#263a34",
        "accent_primary": "#10b981",
        "accent_hover": "#059669",
        "accent_subtle": "#047857",
        "text_primary": "#ecfdf5",
        "text_secondary": "#a7f3d0",
        "text_muted": "#6ee7b7",
        "border_color": "#334e45",
        "danger": "#f43f5e",
        "warning": "#eab308",
        "info": "#06b6d4",
    },
}

DEFAULT_THEME_ID = "sims4_dark_green"


class ThemeManager:
    """
    Manages loading, parsing, and applying color palettes and styles for CustomTkinter.
    Ensures that theme colors are strictly decoupled from hardcoded strings.
    """

    def __init__(self, theme_id: str = DEFAULT_THEME_ID, custom_palette: Optional[Dict[str, str]] = None):
        self.theme_id = theme_id
        self.palette: Dict[str, str] = {}
        self.theme_name: str = "Default Theme"

        base_palette = dict(BUILTIN_PALETTES.get(theme_id, BUILTIN_PALETTES[DEFAULT_THEME_ID]))
        if custom_palette:
            base_palette.update(custom_palette)
            self.palette = base_palette
            self.theme_name = "Custom Theme"
        else:
            self.palette = base_palette
            self.theme_name = theme_id

    @classmethod
    def from_theme_json(cls, theme_json_path: str) -> "ThemeManager":
        """
        Loads theme configuration from a store theme.json file.
        Falls back gracefully to default palette if file cannot be read.
        """
        path = Path(theme_json_path)
        if not path.is_file():
            logger.warning(f"Theme file '{path}' not found. Falling back to {DEFAULT_THEME_ID}")
            return cls(DEFAULT_THEME_ID)

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            theme_id = data.get("theme_id", DEFAULT_THEME_ID)
            palette = data.get("palette", {})
            instance = cls(theme_id=theme_id, custom_palette=palette)
            instance.theme_name = data.get("theme_name", theme_id)
            return instance
        except Exception as e:
            logger.error(f"Error parsing theme file '{path}': {e}. Using fallback.")
            return cls(DEFAULT_THEME_ID)

    def get_color(self, color_key: str, fallback: str = "#ffffff") -> str:
        """
        Retrieves hex color code from active palette.
        """
        return self.palette.get(color_key, fallback)

    @property
    def background_dark(self) -> str:
        return self.get_color("background_dark", "#121815")

    @property
    def surface_dark(self) -> str:
        return self.get_color("surface_dark", "#1a2420")

    @property
    def surface_secondary(self) -> str:
        return self.get_color("surface_secondary", "#24322c")

    @property
    def accent_primary(self) -> str:
        return self.get_color("accent_primary", "#2ecc71")

    @property
    def accent_hover(self) -> str:
        return self.get_color("accent_hover", "#27ae60")

    @property
    def accent_subtle(self) -> str:
        return self.get_color("accent_subtle", "#165b32")

    @property
    def text_primary(self) -> str:
        return self.get_color("text_primary", "#f0fdf4")

    @property
    def text_secondary(self) -> str:
        return self.get_color("text_secondary", "#a7f3d0")

    @property
    def text_muted(self) -> str:
        return self.get_color("text_muted", "#6ee7b7")

    @property
    def border_color(self) -> str:
        return self.get_color("border_color", "#2d4a3e")

    @property
    def danger(self) -> str:
        return self.get_color("danger", "#e74c3c")

    @property
    def warning(self) -> str:
        return self.get_color("warning", "#f39c12")

    @property
    def info(self) -> str:
        return self.get_color("info", "#3498db")

    def get_font_tuple(self, family: str = "Segoe UI", size: int = 12, weight: str = "normal") -> Tuple[str, int, str]:
        """
        Returns font specification tuple for Tkinter/CustomTkinter.
        """
        return (family, size, weight)

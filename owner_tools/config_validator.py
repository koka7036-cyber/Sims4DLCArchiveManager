"""
Store Configuration and Profile Validator for Owner Tools.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 5.1 and 12.2.
Performs thorough schema, URL, palette, and security audits on store profiles.
"""

import re
import json
from pathlib import Path
from typing import Dict, List, Any, Tuple

from core.logger import get_logger

logger = get_logger("config_validator")

# Regex pattern for store_id
STORE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

# Valid public Yandex.Disk URL prefixes
VALID_YANDEX_PREFIXES = (
    "https://disk.yandex.ru/d/",
    "https://disk.yandex.ru/i/",
    "https://disk.yandex.com/d/",
    "https://yadi.sk/d/",
    "https://yadi.sk/i/",
)

HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")

# Prohibited keys in public store config to prevent credential leaks
PROHIBITED_CONFIG_KEYS = {
    "password",
    "secret",
    "private_key",
    "oauth_token",
    "api_key",
    "dpapi",
    "token",
    "master_key",
    "vault",
}


class ConfigValidationError(Exception):
    """Raised when store configuration validation fails."""
    pass


class ConfigValidator:
    """
    Validates store configuration profiles before release generation.
    """

    @classmethod
    def validate_store_id(cls, store_id: str) -> None:
        if not store_id or not isinstance(store_id, str):
            raise ConfigValidationError("store_id must be a non-empty string.")
        if ".." in store_id or "/" in store_id or "\\" in store_id:
            raise ConfigValidationError(f"Path traversal detected in store_id: '{store_id}'")
        if not STORE_ID_PATTERN.match(store_id):
            raise ConfigValidationError(
                f"Invalid store_id format: '{store_id}'. Only alphanumeric characters, dashes, and underscores allowed."
            )

    @classmethod
    def validate_store_json(cls, store_json_path: Path) -> Dict[str, Any]:
        """Validates schema and content of store.json."""
        p = Path(store_json_path)
        if not p.is_file():
            raise ConfigValidationError(f"store.json not found: {p}")

        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            raise ConfigValidationError(f"Invalid JSON in store.json: {e}") from e

        # Validate store_id
        store_id = data.get("store_id")
        cls.validate_store_id(store_id)

        # Check required fields
        for req in ["store_name", "application_name", "sources"]:
            if not data.get(req):
                raise ConfigValidationError(f"Missing required field in store.json: '{req}'")

        sources = data.get("sources", {})
        if not isinstance(sources, dict) or "primary_dlc_folder" not in sources:
            raise ConfigValidationError("sources must be a dict containing 'primary_dlc_folder'.")

        primary_url = sources["primary_dlc_folder"]
        if not any(primary_url.startswith(prefix) for prefix in VALID_YANDEX_PREFIXES):
            raise ConfigValidationError(
                f"Invalid primary_dlc_folder URL: '{primary_url}'. Must be a public Yandex.Disk URL."
            )

        helper_url = sources.get("helper_archive_url")
        if helper_url and not any(helper_url.startswith(prefix) for prefix in VALID_YANDEX_PREFIXES):
            raise ConfigValidationError(
                f"Invalid helper_archive_url: '{helper_url}'. Must be a public Yandex.Disk URL."
            )

        # Security check: zero secrets in store.json
        cls._scan_for_secrets(data, "store.json")

        return data

    @classmethod
    def validate_theme_json(cls, theme_json_path: Path) -> Dict[str, Any]:
        """Validates schema and palette of theme.json."""
        p = Path(theme_json_path)
        if not p.is_file():
            raise ConfigValidationError(f"theme.json not found: {p}")

        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            raise ConfigValidationError(f"Invalid JSON in theme.json: {e}") from e

        if not data.get("theme_id"):
            raise ConfigValidationError("theme.json missing 'theme_id'.")

        palette = data.get("palette")
        if not isinstance(palette, dict):
            raise ConfigValidationError("theme.json missing 'palette' dictionary.")

        # Validate hex color codes
        for color_name, hex_val in palette.items():
            if not isinstance(hex_val, str) or not HEX_COLOR_PATTERN.match(hex_val):
                raise ConfigValidationError(
                    f"Invalid hex color in theme.json: '{color_name}' = '{hex_val}' (expected #RRGGBB)."
                )

        cls._scan_for_secrets(data, "theme.json")
        return data

    @classmethod
    def validate_store_profile(cls, store_dir: Path) -> Dict[str, Any]:
        """
        Validates complete store profile directory (store.json, theme.json, assets).
        """
        s_dir = Path(store_dir)
        if not s_dir.is_dir():
            raise ConfigValidationError(f"Store directory does not exist: {s_dir}")

        store_data = cls.validate_store_json(s_dir / "store.json")
        theme_data = cls.validate_theme_json(s_dir / "theme.json")

        logger.info(f"Store profile '{store_data['store_id']}' successfully validated.")
        return {
            "store_id": store_data["store_id"],
            "store_data": store_data,
            "theme_data": theme_data,
            "directory": str(s_dir)
        }

    @classmethod
    def _scan_for_secrets(cls, data: Any, context_name: str) -> None:
        """Recursively checks that no owner credentials or private keys exist in config data."""
        if isinstance(data, dict):
            for k, v in data.items():
                k_lower = str(k).lower()
                for prohibited in PROHIBITED_CONFIG_KEYS:
                    if prohibited in k_lower:
                        raise ConfigValidationError(
                            f"Security violation: prohibited credential key '{k}' found in {context_name}!"
                        )
                cls._scan_for_secrets(v, context_name)
        elif isinstance(data, list):
            for item in data:
                cls._scan_for_secrets(item, context_name)

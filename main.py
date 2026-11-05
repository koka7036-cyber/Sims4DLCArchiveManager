"""
Sims 4 DLC Archive & Install Manager (White Label Edition).
Main entry point.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 3, 4.1, 5.1, and 11.
"""

import sys
import os
import argparse
from pathlib import Path

# Minimum Python runtime requirement: Python 3.12 for Windows production, 3.10+ for Linux CI
MIN_PYTHON_VERSION = (3, 12) if sys.platform == "win32" else (3, 10)


def check_runtime_compatibility() -> bool:
    """Verifies Python version and 64-bit architecture."""
    if not getattr(sys, "frozen", False):
        if sys.version_info < MIN_PYTHON_VERSION:
            print(
                f"Error: Python {MIN_PYTHON_VERSION[0]}.{MIN_PYTHON_VERSION[1]}+ x64 is required. "
                f"Current version is {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}.",
                file=sys.stderr
            )
            return False

        if sys.maxsize <= 2**32:
            print("Error: 64-bit operating system and Python x64 runtime are required.", file=sys.stderr)
            return False

    return True


def resolve_base_directory() -> Path:
    """
    Resolves the base resource directory:
    - If running inside PyInstaller bundle: sys._MEIPASS
    - If running as source script: directory containing main.py
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def detect_store_id(base_dir: Path, cli_override: str = None) -> str:
    """
    Detects target store identifier:
    1. CLI argument (--store-id)
    2. Environment variable (S4_STORE_ID)
    3. Single embedded store in configs/stores/<store_id>
    4. Default: 'store_alpha'
    """
    if cli_override:
        return cli_override

    env_val = os.environ.get("S4_STORE_ID")
    if env_val:
        return env_val

    # Check if exactly one store exists in bundled configs/stores
    stores_dir = base_dir / "configs" / "stores"
    if stores_dir.is_dir():
        subdirs = [d.name for d in stores_dir.iterdir() if d.is_dir()]
        if len(subdirs) == 1:
            return subdirs[0]

    return "store_alpha"


def main(args=None):
    if not check_runtime_compatibility():
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Sims 4 DLC Archive & Install Manager")
    parser.add_argument("--store-id", type=str, default=None, help="Target White Label store profile identifier")
    parser.add_argument("--check-runtime", action="store_true", help="Perform runtime check and exit")
    parser.add_argument("--headless", action="store_true", help="Initialize in headless mode without mainloop")
    parsed = parser.parse_args(args)

    if parsed.check_runtime:
        print("Sims 4 DLC Archive & Install Manager: Runtime check OK")
        return 0

    base_dir = resolve_base_directory()
    store_id = detect_store_id(base_dir, cli_override=parsed.store_id)

    # Lazy import of GUI app layer
    from gui.app import Sims4ManagerApp

    app = Sims4ManagerApp(
        store_id=store_id,
        config_base_dir=str(base_dir / "configs")
    )

    if not parsed.headless:
        app.run()
    else:
        print(f"Sims 4 DLC Archive & Install Manager: Initialized headless for store '{store_id}'")
        return 0


if __name__ == "__main__":
    main()

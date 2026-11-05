"""
Application Entry Point and Bootstrap for GUI.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 5 and 11.
Initializes AppContext, BrandingManager, AppController, and launches MainWindow.
"""

import sys
from pathlib import Path
from typing import Optional

from core.app_context import AppContext, set_current_context
from core.logger import setup_logger, get_logger
from branding.branding_manager import BrandingManager
from gui.controller import AppController
from gui.main_window import MainWindow

logger = get_logger("app")


class Sims4ManagerApp:
    """
    Main application coordinator responsible for loading store configuration,
    ensuring directory isolation, and running the CustomTkinter event loop.
    """

    def __init__(
        self,
        store_id: str = "store_alpha",
        config_base_dir: Optional[str] = None,
        base_appdata_dir: Optional[str] = None
    ):
        self.store_id = store_id
        config_path = Path(config_base_dir or "configs") / "stores" / store_id / "store.json"

        # Load store configuration
        if config_path.is_file():
            self.context = AppContext.from_store_json(str(config_path), base_appdata_dir=base_appdata_dir)
        else:
            self.context = AppContext(store_id=store_id, base_appdata_dir=base_appdata_dir)

        set_current_context(self.context)
        self.context.ensure_directories()

        # Initialize logging
        setup_logger(self.context.log_file_path)
        logger.info(f"Starting {self.context.application_name} ({self.context.store_id})")

        # Initialize branding and controller
        self.branding = BrandingManager(self.context)
        self.controller = AppController(self.context, self.branding)

        # Main Window
        self.window = MainWindow(self.controller)

    def run(self) -> None:
        """Starts CustomTkinter main event loop."""
        # Initial background tasks: scan game installation
        self.controller.scan_game_installation()
        self.window.mainloop()

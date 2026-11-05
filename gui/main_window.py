"""
Main Window for CustomTkinter GUI.
Conforms strictly to PROJECT_SPEC.md v1.1.5 section 11.
Integrates Header, Sidebar, View Container, and Status Bar into a Single Window layout.
"""

from typing import Any, Optional
from gui.ctk_compat import CTk, CTkFrame
from gui.components.sidebar import Sidebar
from gui.components.header import Header
from gui.components.status_bar import StatusBar
from gui.navigation import NavigationController
from gui.views.dashboard_view import DashboardView
from gui.views.catalog_view import CatalogView
from gui.views.downloads_view import DownloadsView
from gui.views.inspector_view import InspectorView
from gui.views.backups_view import BackupsView
from gui.views.history_view import HistoryView
from gui.views.settings_view import SettingsView


class MainWindow(CTk):
    """
    Single Window layout managing the entire CustomTkinter presentation.
    """

    def __init__(self, controller: Any, **kwargs):
        super().__init__(**kwargs)
        self.controller = controller
        self.branding = controller.branding
        self.theme = self.branding.theme

        # Window settings
        self.title(self.branding.application_name)
        self.geometry("1120x720")
        self.minsize(860, 540)
        self.configure(fg_color=self.theme.background_dark)

        self._build_layout()

        # Handle graceful close
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Initial view
        self.navigation.show_view("dashboard")

    def _build_layout(self) -> None:
        # Top Header
        self.header = Header(self, self.branding, self.theme, self.controller)
        self.header.pack(side="top", fill="x")

        # Bottom Status Bar
        self.status_bar = StatusBar(self, self.theme, self.controller)
        self.status_bar.pack(side="bottom", fill="x")

        # Center Body (Sidebar + View Container)
        self.body_frame = CTkFrame(self, fg_color="transparent")
        self.body_frame.pack(side="top", fill="both", expand=True)

        # Left Sidebar
        self.sidebar = Sidebar(
            self.body_frame,
            self.branding,
            self.theme,
            on_navigate=self._on_navigate
        )
        self.sidebar.pack(side="left", fill="y")

        # Right View Container
        self.view_container = CTkFrame(self.body_frame, fg_color=self.theme.background_dark)
        self.view_container.pack(side="right", fill="both", expand=True)

        # Navigation Controller
        self.navigation = NavigationController(self.view_container, self.theme)

        # Instantiate Views
        self.view_dashboard = DashboardView(self.view_container, self.controller, self.theme)
        self.view_catalog = CatalogView(self.view_container, self.controller, self.theme)
        self.view_downloads = DownloadsView(self.view_container, self.controller, self.theme)
        self.view_inspector = InspectorView(self.view_container, self.controller, self.theme)
        self.view_backups = BackupsView(self.view_container, self.controller, self.theme)
        self.view_history = HistoryView(self.view_container, self.controller, self.theme)
        self.view_settings = SettingsView(self.view_container, self.controller, self.theme)

        # Register Views with Navigation
        self.navigation.register_view("dashboard", self.view_dashboard, self.sidebar.buttons.get("dashboard"))
        self.navigation.register_view("catalog", self.view_catalog, self.sidebar.buttons.get("catalog"))
        self.navigation.register_view("downloads", self.view_downloads, self.sidebar.buttons.get("downloads"))
        self.navigation.register_view("inspector", self.view_inspector, self.sidebar.buttons.get("inspector"))
        self.navigation.register_view("backups", self.view_backups, self.sidebar.buttons.get("backups"))
        self.navigation.register_view("history", self.view_history, self.sidebar.buttons.get("history"))
        self.navigation.register_view("settings", self.view_settings, self.sidebar.buttons.get("settings"))

    def _on_navigate(self, key: str) -> None:
        self.navigation.show_view(key)

    def _on_close(self) -> None:
        """Graceful window close protocol."""
        if self.controller.operation_state.is_busy:
            # Cancel downloads or operations safely
            try:
                mgr = self.controller.get_download_manager()
                mgr.cancel_all()
            except Exception:
                pass
        self.quit()

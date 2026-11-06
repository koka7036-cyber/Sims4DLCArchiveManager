"""
Main Window for CustomTkinter GUI.
Conforms strictly to PROJECT_SPEC.md v1.1.5 section 11.
Integrates Header, Sidebar, View Container, and Status Bar into a Single Window layout.
"""

import queue
from typing import Any, Callable, Optional
from core.logger import get_logger
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

logger = get_logger("main_window")


class MainWindow(CTk):
    """
    Single Window layout managing the entire CustomTkinter presentation.
    Implements strict thread-safety: worker threads push callbacks to a Queue;
    the Tkinter main thread drains and applies updates via after() polling.
    """

    def __init__(self, controller: Any, **kwargs):
        super().__init__(**kwargs)
        self.controller = controller
        self.branding = controller.branding
        self.theme = self.branding.theme

        # Thread-safe UI event queue:
        # Worker threads only put tasks here — ZERO Tkinter calls from background threads.
        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._is_closing: bool = False
        self._poll_timer_id: Optional[str] = None

        # Register thread-safe enqueue hook with controller
        self.controller.set_ui_dispatcher(self._enqueue_ui_callback)

        # Window settings
        self.title(self.branding.application_name)
        self.geometry("1120x720")
        self.minsize(860, 540)
        self.configure(fg_color=self.theme.background_dark)

        self._build_layout()

        # Handle graceful close
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Start Tkinter main-thread queue polling loop
        self._start_queue_poller()

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

    def _enqueue_ui_callback(self, fn: Callable[[], None]) -> None:
        """
        Thread-safe entry point invoked from background worker threads.
        Only puts the callback into a thread-safe Queue.
        ZERO Tkinter methods or root.after calls are executed in worker threads.
        """
        if not self._is_closing:
            self._ui_queue.put(fn)

    def _start_queue_poller(self) -> None:
        """Schedules recurring poll of UI queue strictly on the Tkinter main thread."""
        self._poll_timer_id = self.after(25, self._poll_ui_queue)

    def _poll_ui_queue(self) -> None:
        """
        Runs strictly on the Tkinter main thread via recurring after() loop.
        Drains pending UI update callbacks pushed from background threads.
        """
        if self._is_closing:
            return

        self.drain_ui_queue()

        if not self._is_closing:
            self._poll_timer_id = self.after(25, self._poll_ui_queue)

    def drain_ui_queue(self, max_items: int = 50) -> int:
        """
        Synchronously processes up to max_items from the queue on the main thread.
        Safe against widget destruction during shutdown.
        """
        if self._is_closing:
            return 0

        processed = 0
        while not self._ui_queue.empty() and processed < max_items:
            try:
                fn = self._ui_queue.get_nowait()
            except queue.Empty:
                break

            if not self._is_closing:
                try:
                    fn()
                except Exception as e:
                    logger.error(f"Error executing queued UI callback: {e}")
            self._ui_queue.task_done()
            processed += 1
        return processed

    def destroy(self) -> None:
        """Cancels timers, clears queue, and unregisters dispatcher before widget destruction."""
        self._is_closing = True
        self.controller.set_ui_dispatcher(None)
        if self._poll_timer_id:
            try:
                self.after_cancel(self._poll_timer_id)
            except Exception:
                pass
            self._poll_timer_id = None
        # Discard remaining queue callbacks
        while not self._ui_queue.empty():
            try:
                self._ui_queue.get_nowait()
                self._ui_queue.task_done()
            except queue.Empty:
                break
        super().destroy()

    def _on_close(self) -> None:
        """Graceful window close protocol."""
        self._is_closing = True
        self.controller.set_ui_dispatcher(None)

        if self._poll_timer_id:
            try:
                self.after_cancel(self._poll_timer_id)
            except Exception:
                pass
            self._poll_timer_id = None

        # Discard any remaining queued callbacks so destroyed widgets are never called
        while not self._ui_queue.empty():
            try:
                self._ui_queue.get_nowait()
                self._ui_queue.task_done()
            except queue.Empty:
                break

        if self.controller.operation_state.is_busy:
            # Cancel downloads or operations safely
            try:
                mgr = self.controller.get_download_manager()
                for task_id in list(self.controller.active_downloads.keys()):
                    mgr.cancel_task(task_id)
            except Exception:
                pass
        self.quit()

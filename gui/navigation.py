"""
Navigation Controller for Single Window CustomTkinter GUI.
Conforms strictly to PROJECT_SPEC.md v1.1.5 section 11.1.
Manages view transitions and highlights active sidebar tabs.
"""

from typing import Dict, Any, Callable, Optional
from core.logger import get_logger
from gui.ctk_compat import CTkFrame

logger = get_logger("navigation")


class NavigationController:
    """
    Manages view switching within the main container frame and coordinates sidebar state.
    """

    def __init__(self, container: CTkFrame, theme_manager: Any):
        self.container = container
        self.theme = theme_manager
        self.views: Dict[str, Any] = {}
        self.sidebar_buttons: Dict[str, Any] = {}
        self.current_view_key: Optional[str] = None
        self._on_view_changed_callbacks = []

    def register_view(self, key: str, view_instance: Any, sidebar_button: Optional[Any] = None) -> None:
        """
        Registers a view instance under a unique navigation key.
        """
        self.views[key] = view_instance
        if sidebar_button:
            self.sidebar_buttons[key] = sidebar_button

    def add_on_change_listener(self, callback: Callable[[str], None]) -> None:
        self._on_view_changed_callbacks.append(callback)

    def show_view(self, key: str) -> None:
        """
        Hides current view and presents requested view in the container.
        """
        if key not in self.views:
            logger.error(f"Navigation view '{key}' is not registered.")
            return

        # Hide all views
        for v_key, view in self.views.items():
            if hasattr(view, "pack_forget"):
                view.pack_forget()
            elif hasattr(view, "grid_forget"):
                view.grid_forget()

        # Show target view
        target_view = self.views[key]
        if hasattr(target_view, "pack"):
            target_view.pack(fill="both", expand=True)

        # Update sidebar button highlighting
        for b_key, btn in self.sidebar_buttons.items():
            if hasattr(btn, "configure"):
                if b_key == key:
                    btn.configure(
                        fg_color=self.theme.accent_primary,
                        text_color=self.theme.background_dark
                    )
                else:
                    btn.configure(
                        fg_color="transparent",
                        text_color=self.theme.text_secondary
                    )

        self.current_view_key = key
        for cb in self._on_view_changed_callbacks:
            try:
                cb(key)
            except Exception as e:
                logger.error(f"Error in on_change navigation callback: {e}")

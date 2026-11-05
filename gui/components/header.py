"""
Header Top Bar Component.
Displays store application title, active section name, and quick game detection status.
"""

from typing import Any
from gui.ctk_compat import CTkFrame, CTkLabel, CTkFont


class Header(CTkFrame):
    """
    Top application header displaying store title and quick game status indicator.
    """

    def __init__(self, master: Any, branding: Any, theme: Any, controller: Any, **kwargs):
        super().__init__(
            master,
            fg_color=theme.surface_dark,
            border_color=theme.border_color,
            border_width=1,
            height=54,
            **kwargs
        )
        self.branding = branding
        self.theme = theme
        self.controller = controller

        self._build_ui()
        self.controller.add_listener(self.update_header)
        self.update_header()

    def _build_ui(self) -> None:
        self.title_lbl = CTkLabel(
            self,
            text=self.branding.application_name,
            font=CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=self.theme.text_primary
        )
        self.title_lbl.pack(side="left", padx=20)

        # Game status badge on right
        self.game_badge = CTkLabel(
            self,
            text="The Sims 4: Ожидание сканирования",
            font=CTkFont(family="Segoe UI", size=12, weight="bold"),
            text_color=self.theme.warning,
            fg_color=self.theme.surface_secondary,
            corner_radius=6,
            padx=12,
            pady=4
        )
        self.game_badge.pack(side="right", padx=20)

    def update_header(self) -> None:
        info = self.controller.game_info
        if info.found:
            self.game_badge.configure(
                text=f"The Sims 4: Найдена ({info.installed_dlc_count} DLC) • NTFS",
                text_color=self.theme.accent_primary
            )
        else:
            self.game_badge.configure(
                text="The Sims 4: Не найдена",
                text_color=self.theme.warning
            )

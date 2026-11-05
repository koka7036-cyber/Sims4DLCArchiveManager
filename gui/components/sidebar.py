"""
Sidebar Navigation Component.
Displays Store brand title, logo placeholder, and navigation buttons for each view.
"""

from typing import Any, Callable, Dict
from gui.ctk_compat import CTkFrame, CTkLabel, CTkButton, CTkFont


class Sidebar(CTkFrame):
    """
    Vertical navigation sidebar displaying store identity and section triggers.
    """

    def __init__(
        self,
        master: Any,
        branding: Any,
        theme: Any,
        on_navigate: Callable[[str], None],
        **kwargs
    ):
        super().__init__(
            master,
            fg_color=theme.surface_dark,
            border_color=theme.border_color,
            border_width=1,
            width=220,
            **kwargs
        )
        self.branding = branding
        self.theme = theme
        self.on_navigate = on_navigate
        self.buttons: Dict[str, CTkButton] = {}

        self._build_ui()

    def _build_ui(self) -> None:
        # Branding Header
        brand_frame = CTkFrame(self, fg_color="transparent")
        brand_frame.pack(fill="x", padx=16, pady=(24, 20))

        # Brand Badge / Icon
        CTkLabel(
            brand_frame,
            text="◆ " + self.branding.store_name,
            font=CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=self.theme.accent_primary,
            anchor="w"
        ).pack(anchor="w")

        CTkLabel(
            brand_frame,
            text=f"v{self.branding.application_version}",
            font=CTkFont(family="Segoe UI", size=11),
            text_color=self.theme.text_muted,
            anchor="w"
        ).pack(anchor="w", pady=(2, 0))

        # Nav Buttons list
        nav_items = [
            ("dashboard", "Главная (Дашборд)"),
            ("catalog", "Каталог DLC"),
            ("downloads", "Загрузки и очередь"),
            ("inspector", "Инспектор и Dry Run"),
            ("backups", "Бэкапы и откат"),
            ("history", "Журнал операций"),
            ("settings", "Настройки"),
        ]

        for key, label in nav_items:
            btn = CTkButton(
                self,
                text=label,
                font=CTkFont(family="Segoe UI", size=13),
                anchor="w",
                height=40,
                fg_color="transparent",
                hover_color=self.theme.surface_secondary,
                text_color=self.theme.text_secondary,
                command=lambda k=key: self.on_navigate(k)
            )
            btn.pack(fill="x", padx=12, pady=3)
            self.buttons[key] = btn

"""
Dashboard View (Главная).
Conforms strictly to PROJECT_SPEC.md v1.1.5 section 11.1.
Displays cloud connection status, The Sims 4 game status, NTFS check,
helper readiness, summary metrics cards, and quick action buttons.
"""

from typing import Any
from gui.ctk_compat import CTkFrame, CTkLabel, CTkButton, CTkFont
from gui.state import DLCStatus


class DashboardView(CTkFrame):
    """
    Dashboard presentation displaying system status, game location, metrics, and actions.
    """

    def __init__(self, master: Any, controller: Any, theme: Any, **kwargs):
        super().__init__(master, fg_color=theme.background_dark, **kwargs)
        self.controller = controller
        self.theme = theme

        self._build_ui()
        self.controller.add_listener(self.update_view)
        self.update_view()

    def _build_ui(self) -> None:
        # Title section
        self.header_frame = CTkFrame(self, fg_color="transparent")
        self.header_frame.pack(fill="x", padx=24, pady=(24, 12))

        self.title_label = CTkLabel(
            self.header_frame,
            text="Сводная панель управления",
            font=CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color=self.theme.text_primary
        )
        self.title_label.pack(anchor="w")

        self.subtitle_label = CTkLabel(
            self.header_frame,
            text=f"Магазин: {self.controller.branding.store_name} ({self.controller.branding.application_name})",
            font=CTkFont(family="Segoe UI", size=13),
            text_color=self.theme.text_muted
        )
        self.subtitle_label.pack(anchor="w", pady=(2, 0))

        # Status Cards Grid
        self.cards_container = CTkFrame(self, fg_color="transparent")
        self.cards_container.pack(fill="x", padx=24, pady=12)

        # 1. Game Status Card
        self.game_card = CTkFrame(
            self.cards_container,
            fg_color=self.theme.surface_dark,
            border_color=self.theme.border_color,
            border_width=1,
            corner_radius=8
        )
        self.game_card.pack(fill="x", pady=6, ipady=8, ipadx=12)

        self.game_title = CTkLabel(
            self.game_card,
            text="Каталог игры The Sims 4",
            font=CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=self.theme.text_primary
        )
        self.game_title.pack(anchor="w", padx=12, pady=(6, 2))

        self.game_path_label = CTkLabel(
            self.game_card,
            text="Поиск игры не выполнен...",
            font=CTkFont(family="Segoe UI", size=13),
            text_color=self.theme.text_secondary
        )
        self.game_path_label.pack(anchor="w", padx=12)

        self.game_fs_label = CTkLabel(
            self.game_card,
            text="Файловая система: проверка NTFS...",
            font=CTkFont(family="Segoe UI", size=12),
            text_color=self.theme.text_muted
        )
        self.game_fs_label.pack(anchor="w", padx=12, pady=(2, 6))

        # 2. Metrics summary
        self.metrics_frame = CTkFrame(self, fg_color="transparent")
        self.metrics_frame.pack(fill="x", padx=24, pady=12)

        self.metric_dlcs = self._create_metric_card(self.metrics_frame, "Всего DLC", "0")
        self.metric_installed = self._create_metric_card(self.metrics_frame, "Установлено", "0")
        self.metric_downloaded = self._create_metric_card(self.metrics_frame, "Скачано архивов", "0")

        # 3. Quick Action Buttons
        self.actions_frame = CTkFrame(self, fg_color="transparent")
        self.actions_frame.pack(fill="x", padx=24, pady=24)

        self.btn_scan_game = CTkButton(
            self.actions_frame,
            text="Сканировать игру",
            fg_color=self.theme.surface_secondary,
            hover_color=self.theme.accent_subtle,
            text_color=self.theme.text_primary,
            command=self._on_scan_game
        )
        self.btn_scan_game.pack(side="left", padx=(0, 12))

        self.btn_refresh_catalog = CTkButton(
            self.actions_frame,
            text="Обновить каталог из облака",
            fg_color=self.theme.accent_primary,
            hover_color=self.theme.accent_hover,
            text_color=self.theme.background_dark,
            command=self._on_refresh_catalog
        )
        self.btn_refresh_catalog.pack(side="left", padx=12)

        self.btn_helper = CTkButton(
            self.actions_frame,
            text="Запустить DLC Помощник",
            fg_color=self.theme.surface_secondary,
            hover_color=self.theme.accent_hover,
            text_color=self.theme.text_primary,
            command=self._on_run_helper
        )
        self.btn_helper.pack(side="left", padx=12)

    def _create_metric_card(self, parent: Any, label_text: str, default_val: str) -> CTkLabel:
        card = CTkFrame(
            parent,
            fg_color=self.theme.surface_dark,
            border_color=self.theme.border_color,
            border_width=1,
            corner_radius=8
        )
        card.pack(side="left", fill="both", expand=True, padx=6, ipady=12, ipadx=12)

        lbl = CTkLabel(
            card,
            text=label_text,
            font=CTkFont(family="Segoe UI", size=13),
            text_color=self.theme.text_muted
        )
        lbl.pack(anchor="w", padx=12)

        val_lbl = CTkLabel(
            card,
            text=default_val,
            font=CTkFont(family="Segoe UI", size=24, weight="bold"),
            text_color=self.theme.accent_primary
        )
        val_lbl.pack(anchor="w", padx=12, pady=(4, 0))
        return val_lbl

    def update_view(self) -> None:
        """Refreshes labels and status indicators based on controller state."""
        info = self.controller.game_info
        if info.found:
            self.game_path_label.configure(
                text=f"Путь: {info.path}",
                text_color=self.theme.text_secondary
            )
            self.game_fs_label.configure(
                text=f"Файловая система: {info.filesystem} (Проверено OK) | Установлено DLC: {info.installed_dlc_count}",
                text_color=self.theme.accent_primary
            )
        else:
            msg = info.error_message or "Игра не обнаружена в реестре или Steam."
            self.game_path_label.configure(text=msg, text_color=self.theme.warning)
            self.game_fs_label.configure(
                text="Файловая система: Ожидание обнаружения игры",
                text_color=self.theme.text_muted
            )

        # Update metrics
        total = len(self.controller.dlc_items)
        installed = sum(1 for it in self.controller.dlc_items.values() if it.status == DLCStatus.INSTALLED)
        downloaded = sum(1 for it in self.controller.dlc_items.values() if it.status in (DLCStatus.DOWNLOADED, DLCStatus.READY_TO_INSTALL))

        self.metric_dlcs.configure(text=str(total))
        self.metric_installed.configure(text=str(installed))
        self.metric_downloaded.configure(text=str(downloaded))

    def _on_scan_game(self) -> None:
        self.controller.scan_game_installation()

    def _on_refresh_catalog(self) -> None:
        self.controller.refresh_catalog()

    def _on_run_helper(self) -> None:
        # Triggers helper through User Consent Gate
        from gui.dialogs.consent_dialog import ConsentDialog
        ConsentDialog(self, on_agree=lambda: self.controller.run_post_install_helper(user_agreed=True))

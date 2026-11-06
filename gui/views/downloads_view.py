"""
Downloads View (Загрузки и очередь).
Conforms strictly to PROJECT_SPEC.md v1.1.5 section 11.1.
Displays active download cards with speed, ETA, .part size, Range 206 badge,
progress bar, and cancellation trigger.
"""

from typing import Any
from gui.ctk_compat import (
    CTkFrame,
    CTkScrollableFrame,
    CTkLabel,
    CTkButton,
    CTkProgressBar,
    CTkFont,
)


class DownloadsView(CTkFrame):
    """
    Downloads queue view displaying live download streams, progress, speed, and ETA.
    """

    def __init__(self, master: Any, controller: Any, theme: Any, **kwargs):
        super().__init__(master, fg_color=theme.background_dark, **kwargs)
        self.controller = controller
        self.theme = theme
        self._cards = {}
        self._empty_label = None

        self._build_ui()
        self.controller.add_listener(self.update_view)
        self.update_view()

    def _build_ui(self) -> None:
        self.header_frame = CTkFrame(self, fg_color="transparent")
        self.header_frame.pack(fill="x", padx=24, pady=(24, 12))

        self.title_label = CTkLabel(
            self.header_frame,
            text="Загрузки и очередь архивов",
            font=CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color=self.theme.text_primary
        )
        self.title_label.pack(anchor="w")

        self.subtitle_label = CTkLabel(
            self.header_frame,
            text="Потоковая загрузка с поддержкой HTTP Range (код 206) и контролем целостности .part файлов",
            font=CTkFont(family="Segoe UI", size=13),
            text_color=self.theme.text_muted
        )
        self.subtitle_label.pack(anchor="w", pady=(2, 0))

        # Explicitly show the real download destination so the user never has to guess.
        destination_row = CTkFrame(self.header_frame, fg_color="transparent")
        destination_row.pack(fill="x", pady=(8, 0))
        CTkLabel(
            destination_row,
            text=f"Папка сохранения DLC: {self.controller.context.downloads_dir}",
            font=CTkFont(size=12),
            text_color=self.theme.text_secondary,
            anchor="w"
        ).pack(side="left", fill="x", expand=True)
        self.btn_open_downloads = CTkButton(
            destination_row,
            text="Открыть папку",
            width=120,
            height=28,
            fg_color=self.theme.surface_secondary,
            hover_color=self.theme.accent_subtle,
            text_color=self.theme.text_primary,
            command=self._open_downloads_folder
        )
        self.btn_open_downloads.pack(side="right", padx=(10, 0))

        # Scrollable container for cards
        self.scroll_container = CTkScrollableFrame(
            self,
            fg_color=self.theme.background_dark,
            border_color=self.theme.border_color,
            border_width=1
        )
        self.scroll_container.pack(fill="both", expand=True, padx=24, pady=(12, 24))

    def update_view(self) -> None:
        active = list(self.controller.active_downloads.values())
        active_ids = {item.item_id for item in active}

        # Remove cards for tasks no longer active
        for item_id in list(self._cards.keys()):
            if item_id not in active_ids:
                card_data = self._cards.pop(item_id, None)
                if card_data and "card" in card_data:
                    try:
                        card_data["card"].destroy()
                    except Exception:
                        pass

        if not active:
            if self._empty_label is None:
                self._empty_label = CTkLabel(
                    self.scroll_container,
                    text="Нет активных загрузок в очереди.",
                    font=CTkFont(size=14),
                    text_color=self.theme.text_muted
                )
                self._empty_label.pack(pady=40)
            return

        if self._empty_label is not None:
            try:
                self._empty_label.destroy()
            except Exception:
                pass
            self._empty_label = None

        for item in active:
            if item.item_id in self._cards:
                self._update_download_card(item)
            else:
                self._render_download_card(item)

    def _update_download_card(self, item: Any) -> None:
        card_data = self._cards.get(item.item_id)
        if not card_data:
            return

        pct = int(item.download_progress * 100)
        curr_mb = (item.archive_size * item.download_progress) / (1024 * 1024)
        total_mb = item.archive_size / (1024 * 1024)
        metrics_text = f"{pct}% ({curr_mb:.1f} MB / {total_mb:.1f} MB)   |   Скорость: {item.formatted_speed}   |   Осталось: {item.formatted_eta}"

        try:
            card_data["pbar"].set(item.download_progress)
            card_data["metrics_lbl"].configure(text=metrics_text)
        except Exception:
            pass

    def _render_download_card(self, item: Any) -> None:
        card = CTkFrame(
            self.scroll_container,
            fg_color=self.theme.surface_dark,
            border_color=self.theme.border_color,
            border_width=1,
            corner_radius=8
        )
        card.pack(fill="x", pady=6, ipady=8, ipadx=12)

        # Top line: Title and Range 206 badge
        top_line = CTkFrame(card, fg_color="transparent")
        top_line.pack(fill="x", padx=12, pady=(6, 2))

        CTkLabel(
            top_line,
            text=f"{item.item_id} — {item.archive_name}",
            font=CTkFont(size=14, weight="bold"),
            text_color=self.theme.text_primary
        ).pack(side="left")

        range_badge = CTkLabel(
            top_line,
            text="HTTP 206 Range Resumable",
            font=CTkFont(size=11, weight="bold"),
            text_color=self.theme.accent_primary,
            fg_color=self.theme.surface_secondary,
            corner_radius=4,
            padx=8,
            pady=2
        )
        range_badge.pack(side="right")

        # Progress bar
        pbar = CTkProgressBar(card, fg_color=self.theme.surface_secondary, progress_color=self.theme.accent_primary)
        pbar.pack(fill="x", padx=12, pady=6)
        pbar.set(item.download_progress)

        # Bottom metrics: Downloaded / Total, Speed, ETA
        bot_line = CTkFrame(card, fg_color="transparent")
        bot_line.pack(fill="x", padx=12, pady=(2, 6))

        pct = int(item.download_progress * 100)
        curr_mb = (item.archive_size * item.download_progress) / (1024 * 1024)
        total_mb = item.archive_size / (1024 * 1024)

        metrics_text = f"{pct}% ({curr_mb:.1f} MB / {total_mb:.1f} MB)   |   Скорость: {item.formatted_speed}   |   Осталось: {item.formatted_eta}"
        metrics_lbl = CTkLabel(
            bot_line,
            text=metrics_text,
            font=CTkFont(size=12),
            text_color=self.theme.text_secondary
        )
        metrics_lbl.pack(side="left")

        # Cancel button
        btn_cancel = CTkButton(
            bot_line,
            text="Отмена",
            width=80,
            height=26,
            fg_color=self.theme.surface_secondary,
            hover_color=self.theme.danger,
            text_color=self.theme.text_primary,
            command=lambda it=item: self._cancel_download(it)
        )
        btn_cancel.pack(side="right")

        self._cards[item.item_id] = {
            "card": card,
            "pbar": pbar,
            "metrics_lbl": metrics_lbl,
        }

    def _open_downloads_folder(self) -> None:
        if not self.controller.open_downloads_folder():
            try:
                from gui.dialogs.error_dialog import ErrorDialog
                ErrorDialog(
                    self,
                    "Папка загрузок",
                    f"Не удалось открыть папку загрузок:\n{self.controller.context.downloads_dir}",
                    self.theme,
                )
            except Exception:
                pass

    def _cancel_download(self, item: Any) -> None:
        mgr = self.controller.get_download_manager()
        mgr.cancel_task(item.item_id)

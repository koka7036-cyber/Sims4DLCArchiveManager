"""
History View (История операций и журнал транзакций).
Conforms strictly to PROJECT_SPEC.md v1.1.5 section 11.1.
Displays SQLite WAL operation journal from state.db with file-level audit details.
"""

from typing import Any
from gui.ctk_compat import (
    CTkFrame,
    CTkScrollableFrame,
    CTkLabel,
    CTkButton,
    CTkFont,
)


class HistoryView(CTkFrame):
    """
    History presentation displaying immutable transaction log and file changes from state.db.
    """

    def __init__(self, master: Any, controller: Any, theme: Any, **kwargs):
        super().__init__(master, fg_color=theme.background_dark, **kwargs)
        self.controller = controller
        self.theme = theme

        self._build_ui()
        self.controller.add_listener(self.update_view)
        self.update_view()

    def _build_ui(self) -> None:
        self.header_frame = CTkFrame(self, fg_color="transparent")
        self.header_frame.pack(fill="x", padx=24, pady=(24, 12))

        self.title_label = CTkLabel(
            self.header_frame,
            text="История операций и журнал транзакций",
            font=CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color=self.theme.text_primary
        )
        self.title_label.pack(anchor="w")

        self.subtitle_label = CTkLabel(
            self.header_frame,
            text="Пофайловый аудит выполненных изменений из базы данных state.db (SQLite WAL, synchronous=FULL)",
            font=CTkFont(family="Segoe UI", size=13),
            text_color=self.theme.text_muted
        )
        self.subtitle_label.pack(anchor="w", pady=(2, 0))

        # Log Table Header
        self.header_row = CTkFrame(
            self,
            fg_color=self.theme.surface_dark,
            border_color=self.theme.border_color,
            border_width=1,
            height=36
        )
        self.header_row.pack(fill="x", padx=24, pady=(8, 0))

        CTkLabel(self.header_row, text="ID транзакции", width=220, anchor="w", font=CTkFont(size=12, weight="bold"), text_color=self.theme.text_muted).pack(side="left", padx=12)
        CTkLabel(self.header_row, text="Тип действия", width=120, font=CTkFont(size=12, weight="bold"), text_color=self.theme.text_muted).pack(side="left", padx=8)
        CTkLabel(self.header_row, text="Дата / Время", width=160, font=CTkFont(size=12, weight="bold"), text_color=self.theme.text_muted).pack(side="left", padx=8)
        CTkLabel(self.header_row, text="Статус фиксации", width=160, font=CTkFont(size=12, weight="bold"), text_color=self.theme.text_muted).pack(side="left", padx=8)

        # Scrollable log entries
        self.log_scroll = CTkScrollableFrame(
            self,
            fg_color=self.theme.background_dark,
            border_color=self.theme.border_color,
            border_width=1
        )
        self.log_scroll.pack(fill="both", expand=True, padx=24, pady=(0, 24))

    def update_view(self) -> None:
        for child in self.log_scroll.winfo_children():
            child.destroy()

        try:
            db = self.controller.get_state_db()
            history = db.get_transaction_history(limit=50)
            if not history:
                CTkLabel(
                    self.log_scroll,
                    text="Журнал транзакций пуст. Установка дополнений ещё не производилась.",
                    font=CTkFont(size=13),
                    text_color=self.theme.text_muted
                ).pack(pady=40)
                return

            for tx in history:
                row = CTkFrame(self.log_scroll, fg_color=self.theme.surface_dark, height=36)
                row.pack(fill="x", pady=2)

                CTkLabel(row, text=tx.get("tx_id", ""), width=220, anchor="w", font=CTkFont(size=12, weight="bold"), text_color=self.theme.text_primary).pack(side="left", padx=12)
                CTkLabel(row, text=tx.get("operation_type", "INSTALL"), width=120, font=CTkFont(size=12), text_color=self.theme.accent_primary).pack(side="left", padx=8)
                CTkLabel(row, text=tx.get("created_at", ""), width=160, font=CTkFont(size=12), text_color=self.theme.text_secondary).pack(side="left", padx=8)

                status = tx.get("status", "COMMITTED")
                status_color = self.theme.accent_primary if status == "COMMITTED" else self.theme.warning
                CTkLabel(row, text=status, width=160, font=CTkFont(size=12, weight="bold"), text_color=status_color).pack(side="left", padx=8)

        except Exception as e:
            CTkLabel(
                self.log_scroll,
                text=f"Не удалось загрузить журнал операций: {e}",
                font=CTkFont(size=13),
                text_color=self.theme.danger
            ).pack(pady=40)

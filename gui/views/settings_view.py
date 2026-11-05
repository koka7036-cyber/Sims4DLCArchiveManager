"""
Settings View (Пользовательские настройки и информация о магазине).
Conforms strictly to PROJECT_SPEC.md v1.1.5 section 11.1.
Displays active White Label store configuration, isolated %LOCALAPPDATA% storage paths,
download concurrency limits, and Crash Recovery status.
"""

from typing import Any
from gui.ctk_compat import (
    CTkFrame,
    CTkLabel,
    CTkButton,
    CTkFont,
)


class SettingsView(CTkFrame):
    """
    Settings presentation showing store details, storage directories, and preferences.
    """

    def __init__(self, master: Any, controller: Any, theme: Any, **kwargs):
        super().__init__(master, fg_color=theme.background_dark, **kwargs)
        self.controller = controller
        self.theme = theme

        self._build_ui()

    def _build_ui(self) -> None:
        self.header_frame = CTkFrame(self, fg_color="transparent")
        self.header_frame.pack(fill="x", padx=24, pady=(24, 12))

        self.title_label = CTkLabel(
            self.header_frame,
            text="Настройки и сведения о магазине",
            font=CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color=self.theme.text_primary
        )
        self.title_label.pack(anchor="w")

        self.subtitle_label = CTkLabel(
            self.header_frame,
            text="White Label профиль, изолированные каталоги данных и параметры загрузчика",
            font=CTkFont(family="Segoe UI", size=13),
            text_color=self.theme.text_muted
        )
        self.subtitle_label.pack(anchor="w", pady=(2, 0))

        # 1. Store Profile Info Card
        self.store_card = CTkFrame(
            self,
            fg_color=self.theme.surface_dark,
            border_color=self.theme.border_color,
            border_width=1,
            corner_radius=8
        )
        self.store_card.pack(fill="x", padx=24, pady=12, ipady=12, ipadx=12)

        CTkLabel(
            self.store_card,
            text="Профиль магазина (White Label)",
            font=CTkFont(size=15, weight="bold"),
            text_color=self.theme.text_primary
        ).pack(anchor="w", padx=12, pady=(4, 8))

        ctx = self.controller.context
        self._add_info_row(self.store_card, "Идентификатор магазина (store_id):", ctx.store_id)
        self._add_info_row(self.store_card, "Наименование магазина:", ctx.store_name)
        self._add_info_row(self.store_card, "Наименование приложения:", ctx.application_name)
        self._add_info_row(self.store_card, "Версия сборки:", ctx.application_version)
        self._add_info_row(self.store_card, "Тема оформления:", f"{self.theme.theme_name} ({self.theme.theme_id})")

        # 2. Isolated Storage Paths Card
        self.paths_card = CTkFrame(
            self,
            fg_color=self.theme.surface_dark,
            border_color=self.theme.border_color,
            border_width=1,
            corner_radius=8
        )
        self.paths_card.pack(fill="x", padx=24, pady=12, ipady=12, ipadx=12)

        CTkLabel(
            self.paths_card,
            text="Изолированные хранилища (%LOCALAPPDATA%)",
            font=CTkFont(size=15, weight="bold"),
            text_color=self.theme.text_primary
        ).pack(anchor="w", padx=12, pady=(4, 8))

        self._add_info_row(self.paths_card, "Базы данных (state.db / catalog.db):", str(ctx.database_dir))
        self._add_info_row(self.paths_card, "Каталог загрузок архивов (.part):", str(ctx.downloads_dir))
        self._add_info_row(self.paths_card, "Пользовательские бэкапы:", str(ctx.user_backups_dir))
        self._add_info_row(self.paths_card, "Файловые блокировки (locks):", str(ctx.locks_dir))
        self._add_info_row(self.paths_card, "Файл журнала (app.log):", str(ctx.log_file_path))

        # 3. Actions Card
        self.actions_card = CTkFrame(
            self,
            fg_color=self.theme.surface_dark,
            border_color=self.theme.border_color,
            border_width=1,
            corner_radius=8
        )
        self.actions_card.pack(fill="x", padx=24, pady=12, ipady=12, ipadx=12)

        CTkLabel(
            self.actions_card,
            text="Сервисные операции",
            font=CTkFont(size=15, weight="bold"),
            text_color=self.theme.text_primary
        ).pack(anchor="w", padx=12, pady=(4, 8))

        actions_row = CTkFrame(self.actions_card, fg_color="transparent")
        actions_row.pack(fill="x", padx=12, pady=4)

        self.btn_check_integrity = CTkButton(
            actions_row,
            text="Проверить согласованность БД",
            fg_color=self.theme.surface_secondary,
            hover_color=self.theme.accent_subtle,
            text_color=self.theme.text_primary,
            command=self._check_db_integrity
        )
        self.btn_check_integrity.pack(side="left", padx=(0, 10))

    def _add_info_row(self, parent: Any, label: str, val: str) -> None:
        row = CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=2)
        CTkLabel(row, text=label, width=280, anchor="w", font=CTkFont(size=12, weight="bold"), text_color=self.theme.text_secondary).pack(side="left")
        CTkLabel(row, text=val, anchor="w", font=CTkFont(size=12), text_color=self.theme.text_muted).pack(side="left", fill="x", expand=True)

    def _check_db_integrity(self) -> None:
        try:
            db = self.controller.get_state_db()
            db.check_integrity()
            from gui.dialogs.confirmation_dialog import ConfirmationDialog
            ConfirmationDialog(
                self,
                title="Целостность баз данных",
                message="PRAGMA quick_check: База данных state.db полностью целостна и согласована.",
                theme=self.theme,
                confirm_only=True
            )
        except Exception as e:
            from gui.dialogs.error_dialog import ErrorDialog
            msg = self.controller.translate_exception(e)
            ErrorDialog(self, "Ошибка целостности БД", msg, self.theme)

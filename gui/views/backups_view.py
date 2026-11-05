"""
Backups and Rollback View (Резервные копии и откат).
Conforms strictly to PROJECT_SPEC.md v1.1.5 section 11.1.
Enables creating user backups (Saves, Tray, Mods), managing rollback points,
and initiating transactional rollbacks via BackupManager and CrashRecoveryEngine.
"""

from typing import Any
from gui.ctk_compat import (
    CTkFrame,
    CTkScrollableFrame,
    CTkLabel,
    CTkButton,
    CTkFont,
)


class BackupsView(CTkFrame):
    """
    Backups presentation for user data protection and transactional rollbacks.
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
            text="Резервные копии и откат транзакций",
            font=CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color=self.theme.text_primary
        )
        self.title_label.pack(anchor="w")

        self.subtitle_label = CTkLabel(
            self.header_frame,
            text="Создание резервных копий Saves, Tray, Mods и атомарный откат файловых модификаций",
            font=CTkFont(family="Segoe UI", size=13),
            text_color=self.theme.text_muted
        )
        self.subtitle_label.pack(anchor="w", pady=(2, 0))

        # 1. User Data Backups Card
        self.user_backup_card = CTkFrame(
            self,
            fg_color=self.theme.surface_dark,
            border_color=self.theme.border_color,
            border_width=1,
            corner_radius=8
        )
        self.user_backup_card.pack(fill="x", padx=24, pady=12, ipady=12, ipadx=12)

        CTkLabel(
            self.user_backup_card,
            text="Резервные копии данных пользователя (The Sims 4)",
            font=CTkFont(size=15, weight="bold"),
            text_color=self.theme.text_primary
        ).pack(anchor="w", padx=12, pady=(4, 8))

        btn_row = CTkFrame(self.user_backup_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=4)

        self.btn_backup_saves = CTkButton(
            btn_row,
            text="Бэкап Сохранений (Saves)",
            fg_color=self.theme.surface_secondary,
            hover_color=self.theme.accent_subtle,
            text_color=self.theme.text_primary,
            command=lambda: self._create_user_backup("saves")
        )
        self.btn_backup_saves.pack(side="left", padx=(0, 10))

        self.btn_backup_tray = CTkButton(
            btn_row,
            text="Бэкап Библиотеки (Tray)",
            fg_color=self.theme.surface_secondary,
            hover_color=self.theme.accent_subtle,
            text_color=self.theme.text_primary,
            command=lambda: self._create_user_backup("tray")
        )
        self.btn_backup_tray.pack(side="left", padx=10)

        self.btn_backup_mods = CTkButton(
            btn_row,
            text="Бэкап Модов (Mods)",
            fg_color=self.theme.surface_secondary,
            hover_color=self.theme.accent_subtle,
            text_color=self.theme.text_primary,
            command=lambda: self._create_user_backup("mods")
        )
        self.btn_backup_mods.pack(side="left", padx=10)

        # 2. Transaction Rollback Card
        self.tx_rollback_card = CTkFrame(
            self,
            fg_color=self.theme.surface_dark,
            border_color=self.theme.border_color,
            border_width=1,
            corner_radius=8
        )
        self.tx_rollback_card.pack(fill="x", padx=24, pady=12, ipady=12, ipadx=12)

        CTkLabel(
            self.tx_rollback_card,
            text="Аварийное восстановление и откат транзакций",
            font=CTkFont(size=15, weight="bold"),
            text_color=self.theme.text_primary
        ).pack(anchor="w", padx=12, pady=(4, 4))

        self.recovery_status_lbl = CTkLabel(
            self.tx_rollback_card,
            text="Проверка журнала состояния...",
            font=CTkFont(size=13),
            text_color=self.theme.text_secondary
        )
        self.recovery_status_lbl.pack(anchor="w", padx=12, pady=(2, 8))

        self.btn_rollback_last = CTkButton(
            self.tx_rollback_card,
            text="Безопасный откат последней транзакции",
            fg_color=self.theme.danger,
            hover_color="#c0392b",
            text_color=self.theme.text_primary,
            command=self._on_rollback_last_tx
        )
        self.btn_rollback_last.pack(anchor="w", padx=12, pady=4)

        # 3. Existing Backups List
        CTkLabel(
            self,
            text="Список существующих точек резервного копирования:",
            font=CTkFont(size=14, weight="bold"),
            text_color=self.theme.text_primary
        ).pack(anchor="w", padx=24, pady=(16, 4))

        self.backups_scroll = CTkScrollableFrame(
            self,
            fg_color=self.theme.background_dark,
            border_color=self.theme.border_color,
            border_width=1
        )
        self.backups_scroll.pack(fill="both", expand=True, padx=24, pady=(4, 24))

    def update_view(self) -> None:
        # Check recovery status from state.db
        status_text = self.controller.check_crash_recovery()
        self.recovery_status_lbl.configure(text=status_text)

        for child in self.backups_scroll.winfo_children():
            child.destroy()

        mgr = self.controller.get_backup_manager()
        existing = mgr.list_user_backups()
        if not existing:
            CTkLabel(
                self.backups_scroll,
                text="Резервные копии пользователя пока не созданы.",
                font=CTkFont(size=13),
                text_color=self.theme.text_muted
            ).pack(pady=30)
            return

        for b in existing:
            row = CTkFrame(self.backups_scroll, fg_color=self.theme.surface_dark, height=36)
            row.pack(fill="x", pady=2)
            fname = getattr(b, "archive_filename", b.get("name") if isinstance(b, dict) else str(b))
            created = getattr(b, "created_at", b.get("date") if isinstance(b, dict) else "")
            fpath = getattr(b, "archive_path", b.get("path") if isinstance(b, dict) else str(b))
            CTkLabel(row, text=fname, font=CTkFont(size=13, weight="bold"), text_color=self.theme.text_primary).pack(side="left", padx=12)
            CTkLabel(row, text=str(created), font=CTkFont(size=12), text_color=self.theme.text_muted).pack(side="left", padx=12)
            CTkButton(
                row,
                text="Восстановить",
                width=100,
                height=26,
                fg_color=self.theme.surface_secondary,
                hover_color=self.theme.accent_primary,
                text_color=self.theme.text_primary,
                command=lambda p=fpath: self._restore_user_backup(p)
            ).pack(side="right", padx=12)

    def _create_user_backup(self, backup_type: str) -> None:
        try:
            mgr = self.controller.get_backup_manager()
            mgr.create_user_backup(backup_type)
            self.update_view()
        except Exception as e:
            from gui.dialogs.error_dialog import ErrorDialog
            msg = self.controller.translate_exception(e)
            ErrorDialog(self, "Ошибка создания бэкапа", msg, self.theme)

    def _restore_user_backup(self, backup_target: Any) -> None:
        try:
            mgr = self.controller.get_backup_manager()
            target_path = getattr(backup_target, "archive_path", backup_target.get("path") if isinstance(backup_target, dict) else str(backup_target))
            mgr.restore_user_backup(target_path)
            self.update_view()
        except Exception as e:
            from gui.dialogs.error_dialog import ErrorDialog
            msg = self.controller.translate_exception(e)
            ErrorDialog(self, "Ошибка восстановления бэкапа", msg, self.theme)

    def _on_rollback_last_tx(self) -> None:
        from gui.dialogs.confirmation_dialog import ConfirmationDialog
        ConfirmationDialog(
            self,
            title="Подтверждение отката",
            message="Вы действительно хотите выполнить безопасный откат файлов последней транзакции?",
            theme=self.theme,
            on_confirm=self._execute_rollback
        )

    def _execute_rollback(self) -> None:
        try:
            rec = self.controller.get_recovery_engine()
            db = self.controller.get_state_db()
            incomplete = db.list_active_or_incomplete_transactions()
            if incomplete:
                last_tx = incomplete[0]
                rec.recover_transaction(last_tx["tx_id"])
            self.update_view()
        except Exception as e:
            from gui.dialogs.error_dialog import ErrorDialog
            msg = self.controller.translate_exception(e)
            ErrorDialog(self, "Ошибка отката", msg, self.theme)

"""
Catalog View (Каталог архивов и DLC).
Conforms strictly to PROJECT_SPEC.md v1.1.5 section 11.1.
Displays search, filters (All, Installed, Not Installed, Verify Needed),
DLC table with statuses, selection checkboxes, sizes, and action triggers.
"""

from typing import Any, Dict, List
from gui.ctk_compat import (
    CTkFrame,
    CTkScrollableFrame,
    CTkLabel,
    CTkButton,
    CTkEntry,
    CTkCheckBox,
    CTkFont,
    StringVar,
    BooleanVar,
)
from gui.state import DLCStatus, DLCUIItem


class CatalogView(CTkFrame):
    """
    Catalog presentation allowing searching, filtering, and batch selecting DLCs for download/install.
    """

    def __init__(self, master: Any, controller: Any, theme: Any, **kwargs):
        super().__init__(master, fg_color=theme.background_dark, **kwargs)
        self.controller = controller
        self.theme = theme

        self.search_var = StringVar()
        self.active_filter = "ALL"  # ALL, INSTALLED, NOT_INSTALLED, VERIFY
        self._item_row_widgets = {}

        self._build_ui()
        self.controller.add_listener(self.update_view)
        self.update_view()

    def _build_ui(self) -> None:
        # Title and Controls Header
        self.top_frame = CTkFrame(self, fg_color="transparent")
        self.top_frame.pack(fill="x", padx=24, pady=(24, 12))

        self.title_label = CTkLabel(
            self.top_frame,
            text="Каталог архивов и дополнений (DLC)",
            font=CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color=self.theme.text_primary
        )
        self.title_label.pack(anchor="w")

        # Search Bar and Filter Tabs
        self.filter_frame = CTkFrame(self, fg_color="transparent")
        self.filter_frame.pack(fill="x", padx=24, pady=8)

        self.search_entry = CTkEntry(
            self.filter_frame,
            placeholder_text="Поиск по названию или коду DLC (например, EP01)...",
            fg_color=self.theme.surface_dark,
            border_color=self.theme.border_color,
            text_color=self.theme.text_primary,
            width=320
        )
        self.search_entry.pack(side="left", padx=(0, 12))
        self.search_entry.bind("<KeyRelease>", lambda e: self.update_view())

        # Filter buttons
        self.btn_filter_all = self._create_filter_button("Все", "ALL")
        self.btn_filter_installed = self._create_filter_button("Установленные", "INSTALLED")
        self.btn_filter_not_installed = self._create_filter_button("Не установленные", "NOT_INSTALLED")
        self.btn_filter_verify = self._create_filter_button("Требуют проверки", "VERIFY")

        # Batch Action Bar
        self.actions_bar = CTkFrame(self, fg_color="transparent")
        self.actions_bar.pack(fill="x", padx=24, pady=(4, 12))

        self.btn_select_all = CTkButton(
            self.actions_bar,
            text="Выбрать все",
            fg_color=self.theme.surface_secondary,
            hover_color=self.theme.accent_subtle,
            text_color=self.theme.text_primary,
            width=110,
            command=self._select_all
        )
        self.btn_select_all.pack(side="left", padx=(0, 8))

        self.btn_deselect_all = CTkButton(
            self.actions_bar,
            text="Снять выбор",
            fg_color=self.theme.surface_secondary,
            hover_color=self.theme.accent_subtle,
            text_color=self.theme.text_primary,
            width=110,
            command=self._deselect_all
        )
        self.btn_deselect_all.pack(side="left", padx=8)

        self.btn_download_selected = CTkButton(
            self.actions_bar,
            text="Скачать выбранные",
            fg_color=self.theme.accent_primary,
            hover_color=self.theme.accent_hover,
            text_color=self.theme.background_dark,
            command=self._on_download_selected
        )
        self.btn_download_selected.pack(side="right", padx=(8, 0))

        # Table Header
        self.header_row = CTkFrame(
            self,
            fg_color=self.theme.surface_dark,
            border_color=self.theme.border_color,
            border_width=1,
            height=36
        )
        self.header_row.pack(fill="x", padx=24, pady=(8, 0))

        CTkLabel(self.header_row, text="Выбор", width=60, font=CTkFont(size=12, weight="bold"), text_color=self.theme.text_muted).pack(side="left", padx=4)
        CTkLabel(self.header_row, text="Код / Название", width=160, anchor="w", font=CTkFont(size=12, weight="bold"), text_color=self.theme.text_muted).pack(side="left", padx=8)
        CTkLabel(self.header_row, text="Тип", width=60, font=CTkFont(size=12, weight="bold"), text_color=self.theme.text_muted).pack(side="left", padx=4)
        CTkLabel(self.header_row, text="Размер архива", width=110, font=CTkFont(size=12, weight="bold"), text_color=self.theme.text_muted).pack(side="left", padx=4)
        CTkLabel(self.header_row, text="Распакованный", width=110, font=CTkFont(size=12, weight="bold"), text_color=self.theme.text_muted).pack(side="left", padx=4)
        CTkLabel(self.header_row, text="Статус", width=140, font=CTkFont(size=12, weight="bold"), text_color=self.theme.text_muted).pack(side="left", padx=8)
        CTkLabel(self.header_row, text="Действие", width=120, font=CTkFont(size=12, weight="bold"), text_color=self.theme.text_muted).pack(side="right", padx=12)

        # Scrollable DLC List
        self.table_scroll = CTkScrollableFrame(
            self,
            fg_color=self.theme.background_dark,
            border_color=self.theme.border_color,
            border_width=1
        )
        self.table_scroll.pack(fill="both", expand=True, padx=24, pady=(0, 24))

    def _create_filter_button(self, text: str, filter_key: str) -> CTkButton:
        btn = CTkButton(
            self.filter_frame,
            text=text,
            width=120,
            fg_color=self.theme.accent_primary if self.active_filter == filter_key else self.theme.surface_secondary,
            hover_color=self.theme.accent_hover,
            text_color=self.theme.background_dark if self.active_filter == filter_key else self.theme.text_secondary,
            command=lambda: self._set_filter(filter_key)
        )
        btn.pack(side="left", padx=4)
        return btn

    def _set_filter(self, filter_key: str) -> None:
        self.active_filter = filter_key
        # Update button highlights
        for key, btn in [
            ("ALL", self.btn_filter_all),
            ("INSTALLED", self.btn_filter_installed),
            ("NOT_INSTALLED", self.btn_filter_not_installed),
            ("VERIFY", self.btn_filter_verify),
        ]:
            if key == filter_key:
                btn.configure(fg_color=self.theme.accent_primary, text_color=self.theme.background_dark)
            else:
                btn.configure(fg_color=self.theme.surface_secondary, text_color=self.theme.text_secondary)
        self.update_view()

    def update_view(self) -> None:
        """Renders DLC items filtered by search and active filter tab."""
        search_query = self.search_entry.get().strip().upper()
        items = list(self.controller.dlc_items.values())

        if not items:
            for child in self.table_scroll.winfo_children():
                child.destroy()
            self._item_row_widgets.clear()
            empty_lbl = CTkLabel(
                self.table_scroll,
                text="Каталог пуст. Нажмите «Обновить каталог из облака» на главной панели.",
                font=CTkFont(size=13),
                text_color=self.theme.text_muted
            )
            empty_lbl.pack(pady=40)
            return

        # Check if rows are already rendered and filter/query haven't changed
        current_state_key = (self.active_filter, search_query, len(items))
        if getattr(self, "_last_rendered_key", None) == current_state_key and self._item_row_widgets:
            # Fast in-place status update!
            for item in items:
                row_data = self._item_row_widgets.get(item.item_id)
                if row_data:
                    self._update_row_status(item, row_data)
            return

        self._last_rendered_key = current_state_key
        for child in self.table_scroll.winfo_children():
            child.destroy()
        self._item_row_widgets.clear()

        for item in items:
            # Filter checks
            if search_query and (search_query not in item.item_id.upper() and search_query not in item.title.upper()):
                continue

            if self.active_filter == "INSTALLED" and item.status != DLCStatus.INSTALLED:
                continue
            elif self.active_filter == "NOT_INSTALLED" and item.status == DLCStatus.INSTALLED:
                continue
            elif self.active_filter == "VERIFY" and item.status not in (DLCStatus.DOWNLOADED, DLCStatus.READY_TO_INSTALL, DLCStatus.ERROR):
                continue

            self._render_dlc_row(item)

    def _update_row_status(self, item: DLCUIItem, row_data: Dict[str, Any]) -> None:
        status_color = self.theme.text_muted
        status_text = "Доступно"
        if item.status == DLCStatus.INSTALLED:
            status_color = self.theme.accent_primary
            status_text = "Установлено"
        elif item.status == DLCStatus.DOWNLOADING:
            status_color = self.theme.info
            status_text = f"Загрузка ({int(item.download_progress * 100)}%)"
        elif item.status == DLCStatus.DOWNLOADED:
            status_color = self.theme.warning
            status_text = "Скачано"
        elif item.status == DLCStatus.READY_TO_INSTALL:
            status_color = self.theme.accent_primary
            status_text = "Готово к установке"
        elif item.status == DLCStatus.ERROR:
            status_color = self.theme.danger
            status_text = "Ошибка"

        try:
            row_data["status_lbl"].configure(text=status_text, text_color=status_color)
        except Exception:
            pass

    def _render_dlc_row(self, item: DLCUIItem) -> None:
        row = CTkFrame(
            self.table_scroll,
            fg_color=self.theme.surface_dark,
            border_color=self.theme.border_color,
            border_width=1,
            height=44
        )
        row.pack(fill="x", pady=2)

        # Checkbox
        chk = CTkCheckBox(
            row,
            text="",
            width=30,
            command=lambda it=item: self._toggle_selection(it)
        )
        if item.selected:
            chk.select()
        else:
            chk.deselect()
        chk.pack(side="left", padx=16)

        # Code / Name
        CTkLabel(
            row,
            text=item.item_id,
            width=160,
            anchor="w",
            font=CTkFont(size=13, weight="bold"),
            text_color=self.theme.text_primary
        ).pack(side="left", padx=8)

        # Type
        CTkLabel(
            row,
            text=item.dlc_type,
            width=60,
            font=CTkFont(size=12),
            text_color=self.theme.accent_primary
        ).pack(side="left", padx=4)

        # Sizes
        CTkLabel(row, text=item.formatted_archive_size, width=110, font=CTkFont(size=12), text_color=self.theme.text_secondary).pack(side="left", padx=4)
        CTkLabel(row, text=item.formatted_unpacked_size, width=110, font=CTkFont(size=12), text_color=self.theme.text_muted).pack(side="left", padx=4)

        # Status badge
        status_color = self.theme.text_muted
        status_text = "Доступно"
        if item.status == DLCStatus.INSTALLED:
            status_color = self.theme.accent_primary
            status_text = "Установлено"
        elif item.status == DLCStatus.DOWNLOADING:
            status_color = self.theme.info
            status_text = f"Загрузка ({int(item.download_progress * 100)}%)"
        elif item.status == DLCStatus.DOWNLOADED:
            status_color = self.theme.warning
            status_text = "Скачано"
        elif item.status == DLCStatus.READY_TO_INSTALL:
            status_color = self.theme.accent_primary
            status_text = "Готово к установке"
        elif item.status == DLCStatus.ERROR:
            status_color = self.theme.danger
            status_text = "Ошибка"

        status_lbl = CTkLabel(
            row,
            text=status_text,
            width=140,
            font=CTkFont(size=12, weight="bold"),
            text_color=status_color
        )
        status_lbl.pack(side="left", padx=8)

        # Action button
        action_btn = CTkButton(
            row,
            text="Dry Run",
            width=90,
            height=28,
            fg_color=self.theme.surface_secondary,
            hover_color=self.theme.accent_subtle,
            text_color=self.theme.text_primary,
            command=lambda it=item: self._on_dry_run_item(it)
        )
        action_btn.pack(side="right", padx=12)

        self._item_row_widgets[item.item_id] = {
            "row": row,
            "chk": chk,
            "status_lbl": status_lbl,
            "action_btn": action_btn,
        }

    def _toggle_selection(self, item: DLCUIItem) -> None:
        item.selected = not item.selected

    def _select_all(self) -> None:
        for it in self.controller.dlc_items.values():
            it.selected = True
        self.update_view()

    def _deselect_all(self) -> None:
        for it in self.controller.dlc_items.values():
            it.selected = False
        self.update_view()

    def _on_download_selected(self) -> None:
        self.controller.start_download_selected()

    def _on_dry_run_item(self, item: DLCUIItem) -> None:
        try:
            plan = self.controller.perform_dry_run(item.item_id)
            from gui.dialogs.dry_run_dialog import DryRunDialog
            DryRunDialog(self, item.item_id, plan, self.theme)
        except Exception as e:
            from gui.dialogs.error_dialog import ErrorDialog
            msg = self.controller.translate_exception(e)
            ErrorDialog(self, "Ошибка Dry Run", msg, self.theme)

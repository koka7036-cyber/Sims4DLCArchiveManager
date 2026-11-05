"""
Dry Run Results Dialog.
Displays simulation outcome (CREATE vs OVERWRITE, backup size, 0 disk writes) in a modal.
"""

from typing import Any, Dict
from gui.ctk_compat import (
    CTkToplevel,
    CTkFrame,
    CTkScrollableFrame,
    CTkLabel,
    CTkButton,
    CTkFont,
)


class DryRunDialog(CTkToplevel):
    """
    Modal summarizing Dry Run simulation results for a DLC archive.
    """

    def __init__(self, parent: Any, item_id: str, plan: Dict[str, Any], theme: Any):
        super().__init__(parent)
        self.theme = theme

        self.title(f"Результаты Dry Run: {item_id}")
        self.geometry("600x480")
        self.configure(fg_color=theme.background_dark)

        content = CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=20)

        CTkLabel(
            content,
            text=f"Симуляция установки: {item_id} (0 байт изменений)",
            font=CTkFont(size=16, weight="bold"),
            text_color=theme.accent_primary
        ).pack(anchor="w", pady=(0, 6))

        if hasattr(plan, "planned_creates"):
            creates = plan.planned_creates
            overwrites = [ow[0] if isinstance(ow, (tuple, list)) else str(ow) for ow in plan.planned_overwrites]
            backup_mb = plan.total_backup_bytes_needed / (1024 * 1024)
        else:
            creates = plan.get("create_files", plan.get("planned_creates", []))
            raw_ow = plan.get("overwrite_files", plan.get("planned_overwrites", []))
            overwrites = [ow[0] if isinstance(ow, (tuple, list)) else str(ow) for ow in raw_ow]
            backup_mb = plan.get("required_backup_size_bytes", plan.get("total_backup_bytes_needed", 0)) / (1024 * 1024)

        summary_text = (
            f"• Новых файлов к установке (CREATE): {len(creates)}\n"
            f"• Существующих файлов к замене (OVERWRITE): {len(overwrites)}\n"
            f"• Требуемый объём дискового бэкапа оригиналов: {backup_mb:.2f} MB\n"
            f"• Проверка тома NTFS: OK (атомарное перемещение ReplaceIfExists = FALSE)"
        )

        CTkLabel(
            content,
            text=summary_text,
            font=CTkFont(size=13),
            text_color=theme.text_secondary,
            justify="left"
        ).pack(anchor="w", pady=(0, 12))

        # Files list scroll
        CTkLabel(
            content,
            text="Детализация файловых операций:",
            font=CTkFont(size=12, weight="bold"),
            text_color=theme.text_muted
        ).pack(anchor="w", pady=(4, 2))

        scroll = CTkScrollableFrame(
            content,
            fg_color=theme.surface_dark,
            border_color=theme.border_color,
            border_width=1,
            height=200
        )
        scroll.pack(fill="both", expand=True, pady=(2, 16))

        for f in creates:
            CTkLabel(
                scroll,
                text=f"[CREATE] {f}",
                font=CTkFont(family="Consolas", size=11),
                text_color=theme.accent_primary,
                anchor="w"
            ).pack(anchor="w", padx=8, pady=1)

        for f in overwrites:
            CTkLabel(
                scroll,
                text=f"[OVERWRITE] {f}",
                font=CTkFont(family="Consolas", size=11),
                text_color=theme.warning,
                anchor="w"
            ).pack(anchor="w", padx=8, pady=1)

        if not creates and not overwrites:
            CTkLabel(
                scroll,
                text="Файлы в плане отсутствуют.",
                font=CTkFont(size=12),
                text_color=theme.text_muted
            ).pack(pady=20)

        # Close
        btn_frame = CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=(0, 20))

        btn_close = CTkButton(
            btn_frame,
            text="Закрыть",
            width=100,
            fg_color=theme.accent_primary,
            hover_color=theme.accent_hover,
            text_color=theme.background_dark,
            command=self.destroy
        )
        btn_close.pack(side="right")

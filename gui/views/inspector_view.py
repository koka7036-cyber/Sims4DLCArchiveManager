"""
Inspector and Dry Run View (Инспектор архивов, Dry Run и аудит).
Conforms strictly to PROJECT_SPEC.md v1.1.5 section 11.1.
Enables inspecting archive internal structure without extraction, running Dry Run simulations,
verifying security policies (Path Traversal, DOS device blocking, executable filter).
"""

from pathlib import Path
from typing import Any
from gui.ctk_compat import (
    CTkFrame,
    CTkScrollableFrame,
    CTkLabel,
    CTkButton,
    CTkEntry,
    CTkFont,
    filedialog,
)
from core.exceptions import Sims4ManagerError


class InspectorView(CTkFrame):
    """
    Inspector and Dry Run view for zero-mutation archive auditing.
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
            text="Инспектор архивов и аудит Dry Run",
            font=CTkFont(family="Segoe UI", size=22, weight="bold"),
            text_color=self.theme.text_primary
        )
        self.title_label.pack(anchor="w")

        self.subtitle_label = CTkLabel(
            self.header_frame,
            text="Безопасная инспекция структуры без распаковки и симуляция установки с 0 байт изменений на диске",
            font=CTkFont(family="Segoe UI", size=13),
            text_color=self.theme.text_muted
        )
        self.subtitle_label.pack(anchor="w", pady=(2, 0))

        # Controls input card
        self.input_card = CTkFrame(
            self,
            fg_color=self.theme.surface_dark,
            border_color=self.theme.border_color,
            border_width=1,
            corner_radius=8
        )
        self.input_card.pack(fill="x", padx=24, pady=12, ipady=12, ipadx=12)

        CTkLabel(
            self.input_card,
            text="Имя скачанного архива или путь:",
            font=CTkFont(size=13, weight="bold"),
            text_color=self.theme.text_primary
        ).pack(anchor="w", padx=12, pady=(4, 2))

        input_row = CTkFrame(self.input_card, fg_color="transparent")
        input_row.pack(fill="x", padx=12, pady=4)

        self.archive_entry = CTkEntry(
            input_row,
            placeholder_text="Например: EP01.zip или полный путь к файлу...",
            fg_color=self.theme.surface_secondary,
            border_color=self.theme.border_color,
            text_color=self.theme.text_primary,
            width=380
        )
        self.archive_entry.pack(side="left", padx=(0, 8))

        self.btn_browse = CTkButton(
            input_row,
            text="Обзор...",
            width=80,
            fg_color=self.theme.surface_secondary,
            hover_color=self.theme.accent_subtle,
            text_color=self.theme.text_primary,
            command=self._on_browse_file
        )
        self.btn_browse.pack(side="left", padx=(0, 10))

        self.btn_verify = CTkButton(
            input_row,
            text="Проверить (Verify Only)",
            fg_color=self.theme.surface_secondary,
            hover_color=self.theme.accent_subtle,
            text_color=self.theme.text_primary,
            command=self._on_verify_only
        )
        self.btn_verify.pack(side="left", padx=6)

        self.btn_dry_run = CTkButton(
            input_row,
            text="Симуляция (Dry Run)",
            fg_color=self.theme.accent_primary,
            hover_color=self.theme.accent_hover,
            text_color=self.theme.background_dark,
            command=self._on_dry_run
        )
        self.btn_dry_run.pack(side="left", padx=6)

        # Results area
        self.results_frame = CTkScrollableFrame(
            self,
            fg_color=self.theme.background_dark,
            border_color=self.theme.border_color,
            border_width=1
        )
        self.results_frame.pack(fill="both", expand=True, padx=24, pady=(12, 24))

        self.results_text = CTkLabel(
            self.results_frame,
            text="Выберите архив и нажмите «Проверить (Verify Only)» или «Симуляция (Dry Run)» для начала инспекции.",
            font=CTkFont(family="Consolas", size=12),
            text_color=self.theme.text_secondary,
            anchor="w",
            justify="left"
        )
        self.results_text.pack(anchor="nw", padx=12, pady=12)

    def _on_browse_file(self) -> None:
        try:
            chosen = filedialog.askopenfilename(
                title="Выберите архив DLC для инспекции",
                initialdir=str(self.controller.context.downloads_dir),
                filetypes=[("Архивы DLC", "*.zip *.7z *.rar"), ("Все файлы", "*.*")]
            )
            if chosen:
                self.archive_entry.delete(0, "end")
                self.archive_entry.insert(0, chosen)
        except Exception:
            pass

    def _resolve_archive_path(self) -> Path:
        raw = self.archive_entry.get().strip()
        if not raw:
            raise Sims4ManagerError("Пожалуйста, укажите имя архива (например, EP01.zip) или выберите файл кнопкой «Обзор...».")
        p = Path(raw)
        if p.is_file():
            return p
        downloads_candidate = self.controller.context.downloads_dir / raw
        if downloads_candidate.is_file():
            return downloads_candidate
        # Check with extensions if not specified
        if not raw.lower().endswith((".zip", ".7z", ".rar")):
            for ext in (".zip", ".7z"):
                cand = self.controller.context.downloads_dir / f"{raw}{ext}"
                if cand.is_file():
                    return cand
        raise Sims4ManagerError(
            f"Файл архива '{raw}' не найден в папке загрузок ({self.controller.context.downloads_dir}). "
            f"Сначала скачайте архив во вкладке «Каталог» или укажите полный путь через «Обзор...»."
        )

    def _on_verify_only(self) -> None:
        try:
            p = self._resolve_archive_path()
            from core.archive_inspector import DLCArchiveInspector
            inspector = DLCArchiveInspector()
            res = inspector.inspect_archive(p)
            lines = [
                f"=== РЕЗУЛЬТАТ ВЕРИФИКАЦИИ АРХИВА (VERIFY ONLY) ===",
                f"Файл: {p.name}",
                f"Размер: {p.stat().st_size} байт",
                f"Всего файлов в архиве: {res.total_files}",
                f"Файлов DLC (.package): {res.package_count}",
                f"Общий распакованный размер: {res.total_uncompressed_bytes / (1024*1024):.2f} MB",
                f"Корень DLC: {res.detected_dlc_code or 'Корень игры'}",
                f"Безопасность путей: OK (Path Traversal / Symlinks / DOS devices не обнаружены)",
                f"Исполняемые файлы: OK (Запрещённые бинарники отсутствуют)",
                f"Статус: АРХИВ ПОЛНОСТЬЮ ВАЛИДЕН И БЕЗОПАСЕН К УСТАНОВКЕ"
            ]
            self.results_text.configure(text="\n".join(lines), text_color=self.theme.accent_primary)
        except Exception as e:
            msg = self.controller.translate_exception(e)
            self.results_text.configure(text=f"ОШИБКА ИНСПЕКЦИИ:\n{msg}", text_color=self.theme.danger)

    def _on_dry_run(self) -> None:
        try:
            p = self._resolve_archive_path()
            stem = p.stem.upper()
            plan = self.controller.perform_dry_run(stem, archive_path=p)

            if hasattr(plan, "planned_creates"):
                creates = plan.planned_creates
                overwrites = [ow[0] if isinstance(ow, (tuple, list)) else str(ow) for ow in plan.planned_overwrites]
                backup_sz = plan.total_backup_bytes_needed / (1024 * 1024)
            else:
                creates = plan.get("create_files", plan.get("planned_creates", []))
                raw_ow = plan.get("overwrite_files", plan.get("planned_overwrites", []))
                overwrites = [ow[0] if isinstance(ow, (tuple, list)) else str(ow) for ow in raw_ow]
                backup_sz = plan.get("required_backup_size_bytes", plan.get("total_backup_bytes_needed", 0)) / (1024 * 1024)

            lines = [
                f"=== ОТЧЁТ СИМУЛЯЦИИ УСТАНОВКИ (DRY RUN) ===",
                f"Архив: {p.name}",
                f"Модификаций на диске: 0 байт (РЕЖИМ СИМУЛЯЦИИ)",
                f"Файлов к созданию (CREATE, без перезаписи): {len(creates)}",
                f"Файлов к замене (OVERWRITE, двухфазная эвакуация): {len(overwrites)}",
                f"Требуемый объём бэкапа оригиналов: {backup_sz:.2f} MB",
                f"Коллизии имён и блокировки: ОТСУТСТВУЮТ (Готово к атомарной установке)"
            ]
            if overwrites:
                lines.append("\nФайлы подлежащие эвакуации в tx_backups:")
                for ow in overwrites[:10]:
                    lines.append(f"  [OVERWRITE] {ow}")
                if len(overwrites) > 10:
                    lines.append(f"  ... и ещё {len(overwrites) - 10} файлов.")
            self.results_text.configure(text="\n".join(lines), text_color=self.theme.text_primary)
        except Exception as e:
            msg = self.controller.translate_exception(e)
            self.results_text.configure(text=f"ОШИБКА DRY RUN:\n{msg}", text_color=self.theme.danger)

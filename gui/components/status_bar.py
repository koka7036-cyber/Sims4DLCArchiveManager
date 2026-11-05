"""
Status Bar Component.
Displays bottom status information, background task activity, and operation progress.
"""

from typing import Any
from gui.ctk_compat import CTkFrame, CTkLabel, CTkProgressBar, CTkFont
from gui.state import TaskState


class StatusBar(CTkFrame):
    """
    Bottom application status bar reporting system messages and background work.
    """

    def __init__(self, master: Any, theme: Any, controller: Any, **kwargs):
        super().__init__(
            master,
            fg_color=theme.surface_dark,
            border_color=theme.border_color,
            border_width=1,
            height=32,
            **kwargs
        )
        self.theme = theme
        self.controller = controller

        self._build_ui()
        self.controller.add_listener(self.update_status)
        self.update_status()

    def _build_ui(self) -> None:
        self.msg_lbl = CTkLabel(
            self,
            text="Готов к работе",
            font=CTkFont(family="Segoe UI", size=11),
            text_color=self.theme.text_secondary
        )
        self.msg_lbl.pack(side="left", padx=16)

        self.progress_bar = CTkProgressBar(
            self,
            width=160,
            height=10,
            fg_color=self.theme.surface_secondary,
            progress_color=self.theme.accent_primary
        )
        # Hidden by default unless busy
        self.busy_badge = CTkLabel(
            self,
            text="IDLE",
            font=CTkFont(family="Segoe UI", size=10, weight="bold"),
            text_color=self.theme.text_muted
        )
        self.busy_badge.pack(side="right", padx=16)

    def update_status(self) -> None:
        st = self.controller.operation_state
        self.msg_lbl.configure(text=st.status_message)

        if st.is_busy:
            self.busy_badge.configure(text=st.task.value, text_color=self.theme.accent_primary)
            if not getattr(self.progress_bar, "_packed", False):
                self.progress_bar.pack(side="right", padx=8)
            self.progress_bar.set(st.progress)
        else:
            self.busy_badge.configure(text="IDLE", text_color=self.theme.text_muted)
            if getattr(self.progress_bar, "_packed", False):
                self.progress_bar.pack_forget()

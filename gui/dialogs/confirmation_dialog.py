"""
Modal Confirmation Dialog.
Presents structured warning / confirmation prompt with user approval callback.
"""

from typing import Any, Callable, Optional
from gui.ctk_compat import CTkToplevel, CTkFrame, CTkLabel, CTkButton, CTkFont


class ConfirmationDialog(CTkToplevel):
    """
    Modal confirmation dialog with Confirm / Cancel actions.
    """

    def __init__(
        self,
        parent: Any,
        title: str,
        message: str,
        theme: Any,
        on_confirm: Optional[Callable[[], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
        confirm_only: bool = False
    ):
        super().__init__(parent)
        self.theme = theme
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel

        self.title(title)
        self.geometry("480x220")
        self.configure(fg_color=theme.background_dark)

        # Message container
        msg_frame = CTkFrame(self, fg_color="transparent")
        msg_frame.pack(fill="both", expand=True, padx=24, pady=20)

        CTkLabel(
            msg_frame,
            text=title,
            font=CTkFont(size=16, weight="bold"),
            text_color=theme.text_primary
        ).pack(anchor="w", pady=(0, 8))

        CTkLabel(
            msg_frame,
            text=message,
            font=CTkFont(size=13),
            text_color=theme.text_secondary,
            wraplength=430,
            justify="left"
        ).pack(anchor="w")

        # Buttons
        btn_frame = CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=(0, 20))

        if not confirm_only:
            btn_cancel = CTkButton(
                btn_frame,
                text="Отмена",
                width=100,
                fg_color=theme.surface_secondary,
                hover_color=theme.accent_subtle,
                text_color=theme.text_primary,
                command=self._on_cancel_action
            )
            btn_cancel.pack(side="right", padx=(8, 0))

        btn_confirm = CTkButton(
            btn_frame,
            text="ОК" if confirm_only else "Подтвердить",
            width=120,
            fg_color=theme.accent_primary,
            hover_color=theme.accent_hover,
            text_color=theme.background_dark,
            command=self._on_confirm_action
        )
        btn_confirm.pack(side="right")

    def _on_confirm_action(self) -> None:
        self.destroy()
        if self.on_confirm:
            self.on_confirm()

    def _on_cancel_action(self) -> None:
        self.destroy()
        if self.on_cancel:
            self.on_cancel()

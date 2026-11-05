"""
Modal Error Dialog.
Displays formatted, actionable Russian error explanations to the user.
"""

from typing import Any
from gui.ctk_compat import CTkToplevel, CTkFrame, CTkLabel, CTkButton, CTkFont


class ErrorDialog(CTkToplevel):
    """
    Modal error dialog with actionable details and dismiss button.
    """

    def __init__(self, parent: Any, title: str, error_message: str, theme: Any):
        super().__init__(parent)
        self.theme = theme

        self.title(title)
        self.geometry("500x240")
        self.configure(fg_color=theme.background_dark)

        content = CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=20)

        # Danger title
        CTkLabel(
            content,
            text=f"Внимание: {title}",
            font=CTkFont(size=16, weight="bold"),
            text_color=theme.danger
        ).pack(anchor="w", pady=(0, 10))

        # Error description
        CTkLabel(
            content,
            text=error_message,
            font=CTkFont(size=13),
            text_color=theme.text_primary,
            wraplength=450,
            justify="left"
        ).pack(anchor="w", pady=(0, 16))

        # Close button
        btn_frame = CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=(0, 20))

        btn_close = CTkButton(
            btn_frame,
            text="Закрыть",
            width=100,
            fg_color=theme.surface_secondary,
            hover_color=theme.accent_subtle,
            text_color=theme.text_primary,
            command=self.destroy
        )
        btn_close.pack(side="right")

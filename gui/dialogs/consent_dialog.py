"""
User Consent Gate Dialog for Post Install Helper.
Conforms strictly to PROJECT_SPEC.md v1.1.5 section 10:
"Перед запуском вспомогательной утилиты отображается обязательное модальное окно
с явным предупреждением пользователя."
"""

from typing import Any, Callable, Optional
from gui.ctk_compat import CTkToplevel, CTkFrame, CTkLabel, CTkButton, CTkFont


class ConsentDialog(CTkToplevel):
    """
    User Consent Gate ensuring explicit user approval before executing helper utility.
    """

    def __init__(self, parent: Any, on_agree: Callable[[], None], on_decline: Optional[Callable[[], None]] = None):
        super().__init__(parent)
        self.on_agree = on_agree
        self.on_decline = on_decline

        theme = getattr(parent, "theme", None)
        bg = theme.background_dark if theme else "#121815"
        txt_primary = theme.text_primary if theme else "#f0fdf4"
        txt_sec = theme.text_secondary if theme else "#a7f3d0"
        warning_color = theme.warning if theme else "#f39c12"
        btn_accent = theme.accent_primary if theme else "#2ecc71"
        btn_hover = theme.accent_hover if theme else "#27ae60"

        self.title("Шлюз подтверждения пользователя (User Consent Gate)")
        self.geometry("520x260")
        self.configure(fg_color=bg)

        content = CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=24, pady=20)

        CTkLabel(
            content,
            text="Запуск внешней утилиты (DLC Помощник)",
            font=CTkFont(size=16, weight="bold"),
            text_color=warning_color
        ).pack(anchor="w", pady=(0, 10))

        message = (
            "Внимание: Вы собираетесь запустить доверенную внешнюю утилиту активации "
            "дополнений в каталоге установленной игры The Sims 4.\n\n"
            "Утилита проверена по белому списку архива и будет запущена в рабочем каталоге игры.\n\n"
            "Вы подтверждаете запуск?"
        )

        CTkLabel(
            content,
            text=message,
            font=CTkFont(size=13),
            text_color=txt_primary,
            wraplength=470,
            justify="left"
        ).pack(anchor="w", pady=(0, 16))

        btn_frame = CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=24, pady=(0, 20))

        btn_decline = CTkButton(
            btn_frame,
            text="Отклонить",
            width=110,
            fg_color="#334155",
            hover_color="#1e293b",
            text_color=txt_primary,
            command=self._on_decline_action
        )
        btn_decline.pack(side="right", padx=(8, 0))

        btn_agree = CTkButton(
            btn_frame,
            text="Подтвердить запуск",
            width=160,
            fg_color=btn_accent,
            hover_color=btn_hover,
            text_color="#121815",
            command=self._on_agree_action
        )
        btn_agree.pack(side="right")

    def _on_agree_action(self) -> None:
        self.destroy()
        if self.on_agree:
            self.on_agree()

    def _on_decline_action(self) -> None:
        self.destroy()
        if self.on_decline:
            self.on_decline()

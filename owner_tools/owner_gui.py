"""
Owner GUI Dashboard for Local Administration.
Conforms strictly to PROJECT_SPEC.md v1.1.5 section 4.1.
Runs exclusively on owner workstation from source; excluded from client EXE.
"""

from pathlib import Path
from typing import Any, Optional

from gui.ctk_compat import (
    CTk,
    CTkFrame,
    CTkLabel,
    CTkButton,
    CTkEntry,
    CTkFont,
    StringVar,
)
from owner_tools.key_vault import KeyVaultManager, compute_sha256_fingerprint
from owner_tools.config_validator import ConfigValidator
from owner_tools.release_manager import ReleaseManager


class OwnerDashboardWindow(CTk):
    """
    Local administration panel for key management, profile validation, and release signing.
    """

    def __init__(self, keyring_dir: Optional[Path] = None, **kwargs):
        super().__init__(**kwargs)
        self.key_manager = KeyVaultManager(keyring_dir=keyring_dir)
        self.release_manager = ReleaseManager()

        self.title("Sims 4 DLC Archive Manager - Панель Владельца (Owner Mode)")
        self.geometry("900x600")
        self.minsize(750, 480)
        self.configure(fg_color="#0f172a")

        self.status_var = StringVar(value="Готов к администрированию")
        self._build_ui()

    def _build_ui(self) -> None:
        # Header
        head = CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=24, pady=(20, 10))

        CTkLabel(
            head,
            text="Панель администратора / Владельца (Owner Tools)",
            font=CTkFont(size=20, weight="bold"),
            text_color="#38bdf8"
        ).pack(anchor="w")

        CTkLabel(
            head,
            text="Управление мастер-ключами Ed25519, Key Vault (AES-256-GCM) и цифровой подписью манифестов",
            font=CTkFont(size=12),
            text_color="#94a3b8"
        ).pack(anchor="w", pady=(2, 0))

        # Main cards container
        cards = CTkFrame(self, fg_color="transparent")
        cards.pack(fill="both", expand=True, padx=24, pady=10)

        # 1. Keyring Card
        card_keys = CTkFrame(cards, fg_color="#1e293b", corner_radius=8, border_color="#334155", border_width=1)
        card_keys.pack(fill="x", pady=6, ipady=8, ipadx=12)

        CTkLabel(card_keys, text="1. Мастер-ключ Ed25519 и Key Vault", font=CTkFont(size=14, weight="bold"), text_color="#f8fafc").pack(anchor="w", padx=12, pady=(4, 6))

        btn_row1 = CTkFrame(card_keys, fg_color="transparent")
        btn_row1.pack(fill="x", padx=12, pady=4)

        CTkButton(btn_row1, text="Генерация нового ключа (DPAPI)", fg_color="#0284c7", command=self._on_gen_key).pack(side="left", padx=(0, 10))
        CTkButton(btn_row1, text="Проверить открытый ключ", fg_color="#334155", command=self._on_check_pub_key).pack(side="left", padx=10)

        # 2. Store Profile Validation Card
        card_stores = CTkFrame(cards, fg_color="#1e293b", corner_radius=8, border_color="#334155", border_width=1)
        card_stores.pack(fill="x", pady=6, ipady=8, ipadx=12)

        CTkLabel(card_stores, text="2. Профили магазинов и цифровая подпись", font=CTkFont(size=14, weight="bold"), text_color="#f8fafc").pack(anchor="w", padx=12, pady=(4, 6))

        btn_row2 = CTkFrame(card_stores, fg_color="transparent")
        btn_row2.pack(fill="x", padx=12, pady=4)

        CTkButton(btn_row2, text="Валидация store_alpha", fg_color="#334155", command=lambda: self._on_validate_store("store_alpha")).pack(side="left", padx=(0, 10))
        CTkButton(btn_row2, text="Валидация store_beta", fg_color="#334155", command=lambda: self._on_validate_store("store_beta")).pack(side="left", padx=10)
        CTkButton(btn_row2, text="Подписать релиз store_alpha", fg_color="#10b981", command=lambda: self._on_sign_store("store_alpha")).pack(side="left", padx=10)
        CTkButton(btn_row2, text="Подписать релиз store_beta", fg_color="#10b981", command=lambda: self._on_sign_store("store_beta")).pack(side="left", padx=10)

        # Output Log Box
        self.log_box = CTkLabel(
            self,
            text=self.status_var.get(),
            font=CTkFont(family="Consolas", size=12),
            text_color="#94a3b8",
            anchor="w",
            justify="left"
        )
        self.log_box.pack(fill="x", padx=24, pady=12)

    def _on_gen_key(self) -> None:
        try:
            priv, pub = self.key_manager.generate_and_save_master_key()
            fp = compute_sha256_fingerprint(pub)
            self._set_status(f"Новый ключ Ed25519 успешно сохранён в DPAPI. Public FP: {fp}")
        except Exception as e:
            self._set_status(f"Ошибка генерации ключа: {e}")

    def _on_check_pub_key(self) -> None:
        try:
            pub = self.key_manager.load_public_key()
            fp = compute_sha256_fingerprint(pub)
            self._set_status(f"Открытый ключ активен. Отпечаток: {fp}")
        except Exception as e:
            self._set_status(f"Открытый ключ не найден: {e}")

    def _on_validate_store(self, store_id: str) -> None:
        try:
            path = Path("configs") / "stores" / store_id
            ConfigValidator.validate_store_profile(path)
            self._set_status(f"Профиль магазина '{store_id}' проверен: ошибок не обнаружено.")
        except Exception as e:
            self._set_status(f"Ошибка валидации {store_id}: {e}")

    def _on_sign_store(self, store_id: str) -> None:
        try:
            priv = self.key_manager.load_master_private_key()
            m_path, s_path = self.release_manager.sign_and_save_store_release(store_id, priv)
            self._set_status(f"Релиз '{store_id}' подписан: {m_path.name}, {s_path.name}")
        except Exception as e:
            self._set_status(f"Ошибка подписи релиза: {e}")

    def _set_status(self, msg: str) -> None:
        self.status_var.set(msg)
        self.log_box.configure(text=msg)

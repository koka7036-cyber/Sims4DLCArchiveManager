"""
Owner Token Manager.
Conforms strictly to PROJECT_SPEC.md v1.1.5 sections 4.1, 4.2, and 6.1.
Protects owner personal OAuth credentials using Windows DPAPI in the owner keyring.
Never exposes tokens to client builds or store.json.
"""

from pathlib import Path
from typing import Optional

from core.logger import get_logger
from core.exceptions import SecurityError, DPAPIError
from core.security import encrypt_dpapi, decrypt_dpapi
from owner_tools.key_vault import get_default_owner_keyring_dir

logger = get_logger("token_manager")


class OwnerTokenManager:
    """
    Manages personal owner OAuth credentials for Yandex.Disk private administration.
    Credentials are encrypted via Windows DPAPI and stored exclusively in the owner keyring.
    """

    def __init__(self, keyring_dir: Optional[Path] = None):
        self.keyring_dir = keyring_dir or get_default_owner_keyring_dir()

    @property
    def token_file_path(self) -> Path:
        return self.keyring_dir / "oauth.dat"

    def save_oauth_token(self, token_str: str) -> None:
        """Encrypts token via DPAPI and stores in keyring."""
        if not token_str or not isinstance(token_str, str):
            raise ValueError("Token must be a non-empty string.")

        self.keyring_dir.mkdir(parents=True, exist_ok=True)
        raw_bytes = token_str.strip().encode("utf-8")
        encrypted_blob = encrypt_dpapi(raw_bytes)

        with open(self.token_file_path, "wb") as f:
            f.write(encrypted_blob)

        logger.info("Owner OAuth token safely stored in DPAPI keyring.")

    def load_oauth_token(self) -> str:
        """Decrypts and returns owner OAuth token."""
        if not self.token_file_path.is_file():
            raise SecurityError(f"Owner OAuth token file not found: {self.token_file_path}")

        try:
            with open(self.token_file_path, "rb") as f:
                encrypted_blob = f.read()
            decrypted_bytes = decrypt_dpapi(encrypted_blob)
            return decrypted_bytes.decode("utf-8")
        except Exception as e:
            raise SecurityError(f"Failed to decrypt owner OAuth token: {e}") from e

    def has_oauth_token(self) -> bool:
        """Checks if owner OAuth token exists."""
        return self.token_file_path.is_file()

    def remove_oauth_token(self) -> None:
        """Securely deletes token file."""
        if self.token_file_path.is_file():
            self.token_file_path.unlink()
            logger.info("Owner OAuth token removed.")

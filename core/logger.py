"""
Thread-safe rotating logger with sensitive data masking.
Conforms to PROJECT_SPEC.md v1.1.5 section 4.2 / 5.2.
"""

import os
import re
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional

# Regular expressions for masking sensitive data per PROJECT_SPEC.md requirements
PATTERNS = [
    # Yandex OAuth / Bearer tokens
    (re.compile(r'(?i)(OAuth|Bearer)\s+([A-Za-z0-9_\-\.]{16,})'), r'\1 ***MASKED_TOKEN***'),
    # Generic token, password, API key, lease token, and secret key-values
    (re.compile(r'(?i)(lease_token|resume_secret|password|passwd|secret|token|api_key|api-key|apikey|challenge)\s*[:=]\s*["\']?([a-zA-Z0-9_\-]{6,})["\']?'), r'\1="***MASKED***"'),
    # Hex secrets (32-64 characters hex)
    (re.compile(r'(?i)(secret_hex|master_key_hex|resume_secret_hex)\s*[:=]\s*["\']?([a-fA-F0-9]{32,64})["\']?'), r'\1="***MASKED_HEX***"'),
]

MASKED_REPLACEMENT = "***MASKED***"


def mask_sensitive_text(text: str) -> str:
    """Mask sensitive tokens, secrets, and credentials in the given string."""
    if not isinstance(text, str):
        return text
    result = text
    for pattern, replacement in PATTERNS:
        result = pattern.sub(replacement, result)
    return result


class SensitiveDataFilter(logging.Filter):
    """
    Logging filter that sanitizes record message and arguments to prevent
    sensitive information (lease tokens, resume secrets, OAuth tokens)
    from leaking into logs.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = mask_sensitive_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: mask_sensitive_text(str(v)) if isinstance(v, str) else v for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(mask_sensitive_text(str(v)) if isinstance(v, str) else v for v in record.args)
        return True


class MaskingFormatter(logging.Formatter):
    """Formatter that additionally ensures rendered text is masked."""
    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return mask_sensitive_text(formatted)


_LOGGERS = {}
_ROOT_CONFIGURED = False


def setup_logger(
    log_file_path: Optional[str] = None,
    level: int = logging.INFO,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    console_output: bool = True
) -> logging.Logger:
    """
    Configures and returns the base logger for Sims4DLCArchiveManager.
    Thread-safe and equipped with SensitiveDataFilter and MaskingFormatter.
    """
    global _ROOT_CONFIGURED
    logger = logging.getLogger("Sims4DLCArchiveManager")
    logger.setLevel(level)

    formatter = MaskingFormatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] [%(threadName)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    sensitive_filter = SensitiveDataFilter()

    # Configure console handler once
    if not _ROOT_CONFIGURED:
        logger.handlers.clear()
        if console_output:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(level)
            console_handler.setFormatter(formatter)
            console_handler.addFilter(sensitive_filter)
            logger.addHandler(console_handler)
        _ROOT_CONFIGURED = True

    # Configure rotating file handler if log_file_path is provided
    if log_file_path:
        norm_path = os.path.abspath(log_file_path)
        has_file_handler = any(
            isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", None) == norm_path
            for h in logger.handlers
        )
        if not has_file_handler:
            os.makedirs(os.path.dirname(norm_path), exist_ok=True)
            file_handler = RotatingFileHandler(
                filename=norm_path,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8"
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            file_handler.addFilter(sensitive_filter)
            logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Retrieves a child logger with the given name inheriting root masking rules.
    """
    return logging.getLogger(f"Sims4DLCArchiveManager.{name}")

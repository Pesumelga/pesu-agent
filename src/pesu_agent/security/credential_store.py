"""Windows Secure Credential Store using DPAPI (Data Protection API).

Provides secure, per-user encrypted persistence for external API keys (OpenRouter).
Contracts:
- CREDENTIAL_STORAGE_SCOPE = WINDOWS_CURRENT_USER
- OPENROUTER_KEY_STORED_PLAINTEXT = False
- Zero third-party heavy dependencies (uses Windows crypt32.dll via ctypes).
- Never logs or exposes plaintext API keys in exceptions, logs, or status payloads.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Constants
CREDENTIAL_STORAGE_SCOPE: str = "WINDOWS_CURRENT_USER"
OPENROUTER_KEY_STORED_PLAINTEXT: bool = False
CREDENTIAL_PRECEDENCE: list[str] = ["environment", "secure_store", "missing"]

# Default Storage Path
CREDENTIALS_DIR: str = os.path.expandvars(r"%LOCALAPPDATA%\PESU\credentials")
OPENROUTER_CREDENTIAL_FILE: str = os.path.join(CREDENTIALS_DIR, "openrouter.dat")


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _get_crypt32():
    return ctypes.windll.crypt32


def _get_kernel32():
    return ctypes.windll.kernel32


def dpapi_protect(plaintext: str, description: str = "PESU OpenRouter API Key") -> bytes:
    """Encrypts plaintext string using Windows Current User DPAPI."""
    if not plaintext:
        raise ValueError("Plaintext cannot be empty")

    data_bytes = plaintext.encode("utf-8")
    in_blob = _DATA_BLOB(
        len(data_bytes),
        ctypes.cast(ctypes.create_string_buffer(data_bytes), ctypes.POINTER(ctypes.c_char)),
    )
    out_blob = _DATA_BLOB()

    crypt32 = _get_crypt32()
    kernel32 = _get_kernel32()

    # CRYPTPROTECT_UI_FORBIDDEN = 0x01
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        description,
        None,
        None,
        None,
        0x01,
        ctypes.byref(out_blob),
    )
    if not ok:
        err = ctypes.GetLastError()
        raise OSError(f"CryptProtectData failed with error code {err}")

    try:
        encrypted_bytes = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return encrypted_bytes
    finally:
        kernel32.LocalFree(out_blob.pbData)


def dpapi_unprotect(encrypted_bytes: bytes) -> str:
    """Decrypts ciphertext bytes using Windows Current User DPAPI."""
    if not encrypted_bytes:
        raise ValueError("Ciphertext cannot be empty")

    in_blob = _DATA_BLOB(
        len(encrypted_bytes),
        ctypes.cast(ctypes.create_string_buffer(encrypted_bytes), ctypes.POINTER(ctypes.c_char)),
    )
    out_blob = _DATA_BLOB()

    crypt32 = _get_crypt32()
    kernel32 = _get_kernel32()

    # CRYPTPROTECT_UI_FORBIDDEN = 0x01
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0x01,
        ctypes.byref(out_blob),
    )
    if not ok:
        err = ctypes.GetLastError()
        raise OSError(f"CryptUnprotectData failed with error code {err}")

    try:
        decrypted_bytes = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return decrypted_bytes.decode("utf-8")
    finally:
        kernel32.LocalFree(out_blob.pbData)


class CredentialStore:
    """Thread-safe facade for resolving, saving, and removing credentials."""

    def __init__(self, credential_file: Optional[str] = None):
        self.credential_file = credential_file or OPENROUTER_CREDENTIAL_FILE

    def get_openrouter_api_key(self) -> Tuple[Optional[str], str]:
        """Resolves OpenRouter API key following precedence: env -> secure_store -> missing.

        Returns:
            (key_value, source) where source is 'environment', 'secure_store', or 'missing'.
            The key_value is None if source is 'missing'.
        """
        # 1. Environment variable precedence
        env_key = os.getenv("OPENROUTER_API_KEY")
        if env_key and env_key.strip():
            return env_key.strip(), "environment"

        # 2. Windows secure store
        if os.path.exists(self.credential_file):
            try:
                with open(self.credential_file, "rb") as f:
                    ciphertext = f.read()
                if ciphertext:
                    decrypted = dpapi_unprotect(ciphertext)
                    if decrypted and decrypted.strip():
                        return decrypted.strip(), "secure_store"
            except Exception as ex:
                logger.warning(f"Failed to decrypt credential from {self.credential_file}: {ex}")

        # 3. Missing
        return None, "missing"

    def save_openrouter_api_key(self, key: str) -> None:
        """Encrypts and persists the OpenRouter API key to secure store."""
        if not key or not isinstance(key, str):
            raise ValueError("API key must be a non-empty string")

        trimmed = key.strip()
        if not trimmed:
            raise ValueError("API key cannot be empty whitespace")

        if len(trimmed) > 512:
            raise ValueError("API key length exceeds reasonable bounds (512 chars)")

        if any(c in trimmed for c in "\r\n\x00\x08"):
            raise ValueError("API key contains invalid control characters")

        encrypted_bytes = dpapi_protect(trimmed)

        file_path = Path(self.credential_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        with open(file_path, "wb") as f:
            f.write(encrypted_bytes)

        logger.info(f"OpenRouter credential saved securely to {file_path.name} (DPAPI CurrentUser).")

    def remove_openrouter_api_key(self) -> bool:
        """Removes the stored credential file if present. Does not affect environment variables."""
        if os.path.exists(self.credential_file):
            try:
                os.remove(self.credential_file)
                logger.info("Removed stored OpenRouter credential file.")
                return True
            except Exception as ex:
                logger.error(f"Failed to remove credential file: {ex}")
                return False
        return False

    def get_status(self) -> dict[str, Any]:
        """Returns non-sensitive credential configuration status for UI display."""
        key, source = self.get_openrouter_api_key()
        return {
            "configured": (key is not None),
            "source": source,
            "can_remove": (source == "secure_store"),
        }


# Global canonical instance
default_credential_store = CredentialStore()


def get_openrouter_api_key() -> Tuple[Optional[str], str]:
    return default_credential_store.get_openrouter_api_key()


def save_openrouter_api_key(key: str) -> None:
    default_credential_store.save_openrouter_api_key(key)


def remove_openrouter_api_key() -> bool:
    return default_credential_store.remove_openrouter_api_key()


def get_openrouter_status() -> dict[str, Any]:
    return default_credential_store.get_status()

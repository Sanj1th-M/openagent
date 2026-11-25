"""Small OS-vault boundary for credentials that must not be written to JSON."""

from __future__ import annotations

import sys

_TARGET = "openagent/gemini-api-key"


class CredentialStoreUnavailable(RuntimeError):
    """Raised when the operating-system credential store cannot be used."""


def _windows_credential_manager():
    if sys.platform != "win32":
        raise CredentialStoreUnavailable("No supported operating-system credential store is available.")
    try:
        import win32cred
    except ImportError as exc:
        raise CredentialStoreUnavailable("Windows Credential Manager support is unavailable.") from exc
    return win32cred


def get_gemini_key() -> str | None:
    """Read the Gemini key from Windows Credential Manager, if present."""
    vault = _windows_credential_manager()
    try:
        record = vault.CredRead(_TARGET, vault.CRED_TYPE_GENERIC, 0)
    except Exception as exc:
        # pywin32 exposes a Windows error type that is not stable across releases.
        if getattr(exc, "winerror", None) == 1168:
            return None
        raise CredentialStoreUnavailable("Windows Credential Manager could not be read.") from exc
    secret = record.get("CredentialBlob", b"")
    return secret.decode("utf-16-le") if isinstance(secret, bytes) else str(secret or "")


def save_gemini_key(value: str) -> None:
    """Store a non-empty Gemini key in the OS vault without a plaintext fallback."""
    vault = _windows_credential_manager()
    try:
        vault.CredWrite(
            {
                "Type": vault.CRED_TYPE_GENERIC,
                "TargetName": _TARGET,
                "UserName": "gemini_api_key",
                "CredentialBlob": value.encode("utf-16-le"),
                "Persist": vault.CRED_PERSIST_LOCAL_MACHINE,
            },
            0,
        )
    except Exception as exc:
        raise CredentialStoreUnavailable("Windows Credential Manager could not save the Gemini key.") from exc


def delete_gemini_key() -> None:
    vault = _windows_credential_manager()
    try:
        vault.CredDelete(_TARGET, vault.CRED_TYPE_GENERIC, 0)
    except Exception as exc:
        if getattr(exc, "winerror", None) != 1168:
            raise CredentialStoreUnavailable("Windows Credential Manager could not delete the Gemini key.") from exc

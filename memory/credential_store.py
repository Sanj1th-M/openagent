"""Cross-platform OS-vault and external-secret boundary for sensitive credentials."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Protocol

_SERVICE = "openagent"
_GEMINI_SECRET_ID = "gemini_api_key"
_GEMINI_WIN_TARGET = "openagent/gemini-api-key"
_MAX_WIN_BLOB_BYTES = 2560
_SECRET_ID_RE = re.compile(r"^[A-Za-z0-9_.@:-]{1,128}$")
SECRET_REF_KEY = "$secret_ref"


class CredentialStoreUnavailable(RuntimeError):
    """Raised when no secure credential backend is available or usable."""


class ExternalSecretProvider(Protocol):
    """Protocol for explicitly configured external secret backends."""

    def get(self, secret_id: str) -> str | None: ...

    def set(self, secret_id: str, value: str) -> None: ...

    def delete(self, secret_id: str) -> None: ...


_external_provider: ExternalSecretProvider | None = None


def register_external_provider(provider: ExternalSecretProvider | None) -> None:
    """Register (or clear) an explicit external secret provider."""
    global _external_provider
    _external_provider = provider


def _validate_secret_id(secret_id: str) -> str:
    sid = str(secret_id or "").strip()
    if not _SECRET_ID_RE.fullmatch(sid) or ".." in sid:
        raise CredentialStoreUnavailable("Invalid credential identifier.")
    return sid


def make_secret_ref(secret_id: str) -> dict[str, str]:
    """Build a versioned JSON reference pointing to an externalized secret."""
    return {SECRET_REF_KEY: _validate_secret_id(secret_id)}


def parse_secret_ref(value: object) -> str | None:
    """Return the referenced secret ID if `value` is a secret reference dict."""
    if not isinstance(value, dict) or set(value.keys()) != {SECRET_REF_KEY}:
        return None
    raw = value.get(SECRET_REF_KEY)
    if not isinstance(raw, str):
        return None
    sid = raw.strip()
    if not _SECRET_ID_RE.fullmatch(sid) or ".." in sid:
        return None
    return sid


def _win_target(secret_id: str) -> str:
    return _GEMINI_WIN_TARGET if secret_id == _GEMINI_SECRET_ID else f"{_SERVICE}/{secret_id}"


def _env_var_names(secret_id: str) -> tuple[str, ...]:
    norm = re.sub(r"[^A-Za-z0-9]+", "_", secret_id).strip("_").upper()
    names = [f"OPENAGENT_SECRET_{norm}"]
    if secret_id == _GEMINI_SECRET_ID:
        names.append("GEMINI_API_KEY")
    return tuple(names)


def _read_env_or_cred_dir(secret_id: str) -> str | None:
    for env_name in _env_var_names(secret_id):
        raw = os.environ.get(env_name)
        if raw is not None and raw.strip():
            return raw.strip()

    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY", "").strip()
    if cred_dir:
        base = Path(cred_dir)
        try:
            root = base.resolve()
            if root.is_dir():
                for candidate_name in (secret_id, *_env_var_names(secret_id)):
                    if not _SECRET_ID_RE.fullmatch(candidate_name) or ".." in candidate_name:
                        continue
                    candidate = next((p for p in root.iterdir() if p.name == candidate_name), None)
                    if candidate is not None and candidate.is_file() and not candidate.is_symlink():
                        val = candidate.read_text(encoding="utf-8").strip()
                        if val:
                            return val
        except OSError:
            pass
    return None


def _windows_credential_manager():
    if sys.platform != "win32":
        return None
    try:
        import win32cred
    except ImportError:
        return None
    return win32cred


def _is_secure_keyring_backend(backend: object) -> bool:
    """Accept only genuine OS-backed keyring implementations, never plaintext/fail."""
    if backend is None:
        return False
    mod = (getattr(type(backend), "__module__", "") or "").lower()
    cls = (getattr(type(backend), "__name__", "") or "").lower()

    if "chainer" in mod or "chainer" in cls:
        sub = getattr(backend, "backends", None) or ()
        return any(_is_secure_keyring_backend(item) for item in sub)

    if any(bad in mod or bad in cls for bad in ("fail", "null", "plaintext", "alt", "file")):
        return False

    if sys.platform == "win32":
        return "windows" in mod or "winvault" in cls
    if sys.platform == "darwin":
        return "macos" in mod or "keychain" in cls
    # Linux desktop Secret Service / KWallet requires an active D-Bus session.
    if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return False
    return "secretservice" in mod or "kwallet" in mod


def _secure_keyring():
    try:
        import keyring
    except ImportError:
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:
        return None
    return keyring if _is_secure_keyring_backend(backend) else None


def is_available() -> bool:
    """Return whether a writable secure credential store is active."""
    if _external_provider is not None:
        return True
    if _windows_credential_manager() is not None:
        return True
    return _secure_keyring() is not None


def backend_name() -> str:
    """Non-sensitive name of the active credential backend for diagnostics."""
    if _external_provider is not None:
        return "external"
    if _windows_credential_manager() is not None:
        return "windows-credential-manager"
    kr = _secure_keyring()
    if kr is not None:
        if sys.platform == "darwin":
            return "macos-keychain"
        if sys.platform == "win32":
            return "windows-keyring"
        return "linux-secret-service"
    return "unavailable"


def _win32_get(vault, secret_id: str) -> str | None:
    try:
        record = vault.CredRead(_win_target(secret_id), vault.CRED_TYPE_GENERIC, 0)
    except Exception as exc:
        if getattr(exc, "winerror", None) == 1168:
            return None
        raise CredentialStoreUnavailable("Windows Credential Manager could not be read.") from exc
    secret = record.get("CredentialBlob", b"")
    return secret.decode("utf-16-le") if isinstance(secret, bytes) else str(secret or "")


def _win32_save(vault, secret_id: str, value: str) -> None:
    encoded = value.encode("utf-16-le")
    if len(encoded) > _MAX_WIN_BLOB_BYTES:
        raise CredentialStoreUnavailable("Credential exceeds Windows Credential Manager size limit.")
    try:
        vault.CredWrite(
            {
                "Type": vault.CRED_TYPE_GENERIC,
                "TargetName": _win_target(secret_id),
                "UserName": secret_id,
                "CredentialBlob": encoded,
                "Persist": vault.CRED_PERSIST_LOCAL_MACHINE,
            },
            0,
        )
    except Exception as exc:
        raise CredentialStoreUnavailable("Windows Credential Manager could not save the credential.") from exc
    verified = _win32_get(vault, secret_id)
    if verified != value:
        raise CredentialStoreUnavailable("Windows Credential Manager write verification failed.")


def _win32_delete(vault, secret_id: str) -> None:
    try:
        vault.CredDelete(_win_target(secret_id), vault.CRED_TYPE_GENERIC, 0)
    except Exception as exc:
        if getattr(exc, "winerror", None) != 1168:
            raise CredentialStoreUnavailable("Windows Credential Manager could not delete the credential.") from exc


def get_secret(secret_id: str) -> str | None:
    """Read a secret from env/external provider or the OS credential store."""
    sid = _validate_secret_id(secret_id)
    if _external_provider is not None:
        try:
            val = _external_provider.get(sid)
            if val:
                return val
        except CredentialStoreUnavailable:
            raise
        except Exception as exc:
            raise CredentialStoreUnavailable("External secret provider could not be read.") from exc

    env_val = _read_env_or_cred_dir(sid)
    if env_val:
        return env_val

    vault = _windows_credential_manager()
    if vault is not None:
        return _win32_get(vault, sid)

    kr = _secure_keyring()
    if kr is not None:
        try:
            stored = kr.get_password(_SERVICE, sid)
            return stored if stored else None
        except Exception as exc:
            raise CredentialStoreUnavailable("OS keyring could not be read.") from exc

    raise CredentialStoreUnavailable(
        "No supported operating-system credential store is available. "
        "Configure credentials via Windows Credential Manager, macOS Keychain, "
        "Linux Secret Service, or environment variables."
    )


def save_secret(secret_id: str, value: str) -> None:
    """Persist a secret in the OS vault (or external provider) and verify read-back."""
    sid = _validate_secret_id(secret_id)
    secret = str(value or "")
    if not secret:
        delete_secret(sid)
        return

    if _external_provider is not None:
        try:
            _external_provider.set(sid, secret)
            if _external_provider.get(sid) != secret:
                raise CredentialStoreUnavailable("External secret provider write verification failed.")
            return
        except CredentialStoreUnavailable:
            raise
        except Exception as exc:
            raise CredentialStoreUnavailable("External secret provider could not save the credential.") from exc

    vault = _windows_credential_manager()
    if vault is not None:
        _win32_save(vault, sid, secret)
        return

    kr = _secure_keyring()
    if kr is not None:
        try:
            kr.set_password(_SERVICE, sid, secret)
            if kr.get_password(_SERVICE, sid) != secret:
                raise CredentialStoreUnavailable("OS keyring write verification failed.")
            return
        except CredentialStoreUnavailable:
            raise
        except Exception as exc:
            raise CredentialStoreUnavailable("OS keyring could not save the credential.") from exc

    raise CredentialStoreUnavailable(
        "No writable operating-system credential store is available; "
        "refusing to write plaintext credentials to disk."
    )


def delete_secret(secret_id: str) -> None:
    """Remove a secret from the OS vault (or external provider)."""
    sid = _validate_secret_id(secret_id)

    if _external_provider is not None:
        try:
            _external_provider.delete(sid)
            return
        except CredentialStoreUnavailable:
            raise
        except Exception as exc:
            raise CredentialStoreUnavailable("External secret provider could not delete the credential.") from exc

    vault = _windows_credential_manager()
    if vault is not None:
        _win32_delete(vault, sid)
        return

    kr = _secure_keyring()
    if kr is not None:
        try:
            kr.delete_password(_SERVICE, sid)
            return
        except Exception as exc:
            # keyring raises PasswordDeleteError when absent; treat absent as deleted.
            if "not found" in str(exc).lower() or "does not exist" in str(exc).lower():
                return
            try:
                if kr.get_password(_SERVICE, sid) is None:
                    return
            except Exception:
                pass
            raise CredentialStoreUnavailable("OS keyring could not delete the credential.") from exc

    raise CredentialStoreUnavailable("No writable operating-system credential store is available.")


def get_gemini_key() -> str | None:
    """Read the Gemini API key from the credential store, if present."""
    return get_secret(_GEMINI_SECRET_ID)


def save_gemini_key(value: str) -> None:
    """Store a non-empty Gemini key in the OS vault without a plaintext fallback."""
    save_secret(_GEMINI_SECRET_ID, value)


def delete_gemini_key() -> None:
    """Delete the stored Gemini key from the OS vault."""
    delete_secret(_GEMINI_SECRET_ID)

import functools
import json
import os
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any


class ConfigPermissionError(PermissionError):
    """Raised when configuration file/directory permissions cannot be secured."""

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

BASE_DIR    = get_base_dir()
CONFIG_DIR  = BASE_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "api_keys.json"

def ensure_config_dir() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(CONFIG_DIR, stat.S_IRWXU)
        except OSError:
            pass


def _restrict_existing_config_permissions() -> bool:
    """Ensure existing POSIX settings paths are owner-only before reading secrets."""
    if os.name == "nt":
        return True
    if not CONFIG_FILE.exists():
        return True
    try:
        owner = os.geteuid()
        targets: list[tuple[Path, int]] = [
            (CONFIG_DIR, stat.S_IRWXU),
            (CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR),
        ]
        for path, mode in targets:
            current = path.stat()
            if current.st_uid != owner:
                raise PermissionError("settings path is not owned by the current user")
            current_mode = stat.S_IMODE(current.st_mode)
            required_access = stat.S_IXUSR if path == CONFIG_DIR else stat.S_IRUSR
            if current_mode & 0o077 or current_mode & required_access != required_access:
                os.chmod(path, mode)
                current = path.stat()
                current_mode = stat.S_IMODE(current.st_mode)
                if (
                    current.st_uid != owner
                    or current_mode & 0o077
                    or current_mode & required_access != required_access
                ):
                    raise PermissionError("settings path permissions could not be verified")
    except OSError:
        print("[Config] Cannot secure api_keys.json permissions; refusing to load settings.")
        return False
    return True

def config_exists() -> bool:
    return CONFIG_FILE.exists()


# ── Reading and writing the file ─────────────────────────────────────────────
# Many threads use this file at once: the interface, the conversation loop,
# plugins polling a server, background tasks. Every setter read the file,
# changed one key and rewrote it IN PLACE, which failed two ways:
#
#   * a reader that arrived mid-write saw half a file, parsed nothing, and
#     treated every setting as unset — wake word off, no key, Gemini mode;
#   * a SETTER that arrived mid-write did the same, then wrote its one key into
#     an otherwise empty dict — erasing every other setting for good.
#
# Writes now go to a temporary file that replaces the real one in a single step,
# so a reader sees the old file or the new one and never half of either, and
# setters in this process take turns.
_lock = threading.RLock()


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _lock:
            return fn(*args, **kwargs)
    return wrapper


def _sync_config_directory() -> None:
    """Flush the directory entry after replacing the settings file on POSIX."""
    if os.name == "nt":
        return
    try:
        directory_fd = os.open(CONFIG_DIR, os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(directory_fd)
        except OSError:
            pass
    finally:
        try:
            os.close(directory_fd)
        except OSError:
            pass


def _write_config(data: dict) -> None:
    ensure_config_dir()
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{CONFIG_FILE.name}.",
        suffix=".tmp",
        dir=CONFIG_DIR,
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=4)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, stat.S_IREAD | stat.S_IWRITE)
        for attempt in range(20):
            try:
                os.replace(tmp, CONFIG_FILE)
                _sync_config_directory()
                return
            except PermissionError:
                # Windows: an antivirus scan or a reader can hold the file for a
                # moment. Worth a short wait, never a lost setting.
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _read_for_write() -> dict:
    """The current settings, for a setter to change one of them.

    A file that exists but cannot be parsed is set aside as
    api_keys.json.broken-<time> rather than silently replaced by one key — it
    may be a hand edit with a missing comma, and it still holds the user's key."""
    if not CONFIG_FILE.exists():
        return {}
    if not _restrict_existing_config_permissions():
        raise ConfigPermissionError(
            "Cannot safely read existing settings; refusing to overwrite them."
        )
    last = None
    for _ in range(3):
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError) as e:
            last = e
            time.sleep(0.05)
    backup = CONFIG_FILE.with_name(f"{CONFIG_FILE.name}.broken-{int(time.time())}")
    try:
        os.replace(CONFIG_FILE, backup)
        print(f"[Config] api_keys.json could not be read ({last}) - kept as {backup.name}")
    except Exception:
        pass
    return {}


_SECRET_EXACT_KEYS = frozenset({
    "gemini_api_key",
    "elevenlabs_api_key",
    "api_key",
    "bot_token",
    "pair_code",
    "pairing_code",
    "access_token",
    "refresh_token",
    "auth_token",
    "client_secret",
    "password",
    "secret",
    "token",
    "private_key",
    "credential",
})
_SECRET_SUFFIXES = (
    "_api_key",
    "_token",
    "_secret",
    "_password",
    "_credential",
    "_private_key",
)


def _is_secret_key(key: str) -> bool:
    norm = str(key or "").strip().lower()
    if norm in _SECRET_EXACT_KEYS:
        return True
    return any(norm.endswith(suffix) for suffix in _SECRET_SUFFIXES)


def _provider_secret_id(provider_id: str, key: str) -> str:
    return f"provider.{provider_id}.{key}"


def _plugin_secret_id(namespace: str, key: str) -> str:
    return f"plugin.{namespace}.{key}"


def _store_secret_field(
    secret_id: str,
    raw_value: Any,
    existing_value: Any = None,
    *,
    use_ref: bool = True,
) -> Any:
    """Persist a secret field into the OS vault and return its disk representation."""
    from memory import credential_store

    if credential_store.parse_secret_ref(raw_value) is not None:
        return raw_value

    val = str(raw_value or "").strip() if raw_value is not None else ""
    if val:
        credential_store.save_secret(secret_id, val)
        return credential_store.make_secret_ref(secret_id) if use_ref else None

    existing_ref = credential_store.parse_secret_ref(existing_value)
    if existing_ref is not None:
        credential_store.delete_secret(existing_ref)
    elif isinstance(existing_value, str) and existing_value.strip():
        try:
            credential_store.delete_secret(secret_id)
        except credential_store.CredentialStoreUnavailable:
            pass
    return "" if use_ref else None


def _resolve_secret_field(
    secret_id: str,
    raw_value: Any,
    *,
    on_migrate=None,
) -> str:
    """Resolve a secret from `$secret_ref`, env/external provider, or legacy JSON."""
    from memory import credential_store

    ref_id = credential_store.parse_secret_ref(raw_value)
    if ref_id is not None:
        try:
            return str(credential_store.get_secret(ref_id) or "")
        except credential_store.CredentialStoreUnavailable:
            return ""

    legacy = str(raw_value or "").strip() if isinstance(raw_value, str) else ""
    if not legacy:
        try:
            return str(credential_store.get_secret(secret_id) or "")
        except credential_store.CredentialStoreUnavailable:
            return ""

    if on_migrate is not None:
        try:
            credential_store.save_secret(secret_id, legacy)
            on_migrate(credential_store.make_secret_ref(secret_id))
        except credential_store.CredentialStoreUnavailable:
            pass
    return legacy


def _sanitize_provider_creds_for_write(
    creds: dict,
    existing_creds: dict | None = None,
) -> dict:
    """Externalize provider secret fields before persisting the providers block."""
    prev_map = existing_creds if isinstance(existing_creds, dict) else {}
    out: dict = {}
    for pid, cfg in (creds or {}).items():
        if not isinstance(cfg, dict):
            continue
        prev_cfg = prev_map.get(pid) if isinstance(prev_map.get(pid), dict) else {}
        cleaned: dict = {}
        for k, v in cfg.items():
            if _is_secret_key(k):
                cleaned[k] = _store_secret_field(
                    _provider_secret_id(str(pid), k),
                    v,
                    prev_cfg.get(k),
                    use_ref=True,
                )
            else:
                cleaned[k] = v
        out[pid] = cleaned
    return out


def _sanitize_plugin_ns_for_write(
    namespace: str,
    values: dict,
    existing_values: dict | None = None,
) -> dict:
    """Externalize plugin secret fields before persisting a plugin namespace."""
    prev_map = existing_values if isinstance(existing_values, dict) else {}
    cleaned: dict = {}
    for k, v in (values or {}).items():
        if _is_secret_key(k):
            cleaned[k] = _store_secret_field(
                _plugin_secret_id(namespace, k),
                v,
                prev_map.get(k),
                use_ref=True,
            )
        else:
            cleaned[k] = v
    return cleaned


@_locked
def save_api_keys(gemini_api_key: str) -> None:
    ensure_config_dir()

    data = _read_for_write()
    key = gemini_api_key.strip()
    if key:
        from memory.credential_store import save_gemini_key

        save_gemini_key(key)
        # Remove the legacy copy only after the vault write succeeds.
        data.pop("gemini_api_key", None)
        data.pop("local_only", None)     # a key given is a choice to use Gemini again
    else:
        from memory.credential_store import CredentialStoreUnavailable, delete_gemini_key

        had_legacy = bool(str(data.get("gemini_api_key") or "").strip())
        try:
            delete_gemini_key()
        except CredentialStoreUnavailable:
            if not had_legacy:
                raise
        data.pop("gemini_api_key", None)
    _write_config(data)


def load_api_keys(*, raise_on_permission: bool = False) -> dict:
    if not CONFIG_FILE.exists():
        return {}
    if not _restrict_existing_config_permissions():
        if raise_on_permission:
            raise ConfigPermissionError(
                "Cannot safely read api_keys.json; settings permissions were refused."
            )
        return {}
    for attempt in range(3):
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as e:
            if attempt == 2:
                # Plain text: an emoji here raised UnicodeEncodeError on a
                # Windows console, turning a failed read into a crash.
                print(f"[Config] Failed to load api_keys.json: {e}")
                return {}
            time.sleep(0.05)
    return {}


def get_gemini_key(*, raise_on_permission: bool = False) -> str | None:
    from memory.credential_store import CredentialStoreUnavailable, get_gemini_key as vault_key

    try:
        stored = vault_key()
    except CredentialStoreUnavailable:
        # Existing installations remain usable until an explicit successful
        # migration. New writes never fall back to this legacy plaintext value.
        val = load_api_keys(raise_on_permission=raise_on_permission).get("gemini_api_key")
        return str(val).strip() if isinstance(val, str) and val.strip() else None
    if stored:
        return stored
    legacy = str(load_api_keys(raise_on_permission=raise_on_permission).get("gemini_api_key") or "").strip()
    if not legacy:
        return None
    with _lock:
        try:
            from memory.credential_store import save_gemini_key

            data = _read_for_write()
            current_legacy = str(data.get("gemini_api_key") or "").strip()
            if not current_legacy:
                return vault_key()
            save_gemini_key(current_legacy)
            data.pop("gemini_api_key", None)
            _write_config(data)
            return current_legacy
        except CredentialStoreUnavailable:
            return legacy


def get_elevenlabs_key(*, raise_on_permission: bool = False) -> str | None:
    """Resolve the optional top-level ElevenLabs key via the credential store."""
    raw = load_api_keys(raise_on_permission=raise_on_permission).get("elevenlabs_api_key")

    def _migrate(ref: dict) -> None:
        with _lock:
            data = _read_for_write()
            if isinstance(data.get("elevenlabs_api_key"), str) and data["elevenlabs_api_key"].strip():
                data["elevenlabs_api_key"] = ref
                _write_config(data)

    resolved = _resolve_secret_field("elevenlabs_api_key", raw, on_migrate=_migrate)
    return resolved or None


@_locked
def save_elevenlabs_key(api_key: str) -> None:
    """Store or clear the optional top-level ElevenLabs key in the OS vault."""
    ensure_config_dir()
    data = _read_for_write()
    val = (api_key or "").strip()
    if val:
        data["elevenlabs_api_key"] = _store_secret_field(
            "elevenlabs_api_key", val, data.get("elevenlabs_api_key"), use_ref=True
        )
    else:
        _store_secret_field(
            "elevenlabs_api_key", "", data.get("elevenlabs_api_key"), use_ref=True
        )
        data.pop("elevenlabs_api_key", None)
    _write_config(data)

def is_configured() -> bool:
    key = get_gemini_key()
    return bool(key and len(key) > 15)


def get_assistant_name() -> str:
    """Return the configured assistant name, or 'Jarvis' if not set."""
    val = load_api_keys().get("assistant_name", "Jarvis")
    if not isinstance(val, str) or not val.strip():
        return "Jarvis"
    return val.strip()


def get_user_name() -> str:
    """Return the configured user name for addressing."""
    return load_api_keys().get("user_name", "")


@_locked
def save_assistant_config(assistant_name: str, user_name: str) -> None:
    """Persist assistant name and user name to config."""
    ensure_config_dir()
    data = _read_for_write()
    data["assistant_name"] = assistant_name.strip() or "Jarvis"
    data["user_name"] = user_name.strip()
    _write_config(data)


# ── Assistant voice ──────────────────────────────────────────────────────────
# Gemini Live prebuilt voices. Names are proper nouns — identical in every
# language, so this list is safe to show verbatim in any locale.
AVAILABLE_VOICES = ["Charon", "Puck", "Kore", "Fenrir", "Aoede"]
DEFAULT_VOICE    = "Charon"


def get_voice() -> str:
    """Return the configured Live voice, falling back to the default if unset
    or if the stored value is not a voice we recognise."""
    v = load_api_keys().get("voice_name", DEFAULT_VOICE) or DEFAULT_VOICE
    return v if v in AVAILABLE_VOICES else DEFAULT_VOICE


@_locked
def save_voice(voice_name: str) -> None:
    """Persist the chosen Live voice. Unknown names collapse to the default so a
    bad value can never reach the API and break the session."""
    ensure_config_dir()
    data = _read_for_write()
    v = (voice_name or "").strip()
    data["voice_name"] = v if v in AVAILABLE_VOICES else DEFAULT_VOICE
    _write_config(data)


def get_wake_word_enabled() -> bool:
    """Whether local wake-word gating is on (assistant sleeps until 'Hey Jarvis')."""
    return load_api_keys().get("wake_word_enabled", False)


@_locked
def save_wake_word_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data = _read_for_write()
    data["wake_word_enabled"] = bool(enabled)
    _write_config(data)


def get_push_to_talk_enabled() -> bool:
    """Hold-a-key-to-speak. When on, the mic is closed unless the chord is held."""
    return load_api_keys().get("push_to_talk_enabled", False)


def save_push_to_talk_enabled(enabled: bool) -> None:
    _save_flag("push_to_talk_enabled", enabled)


HUD_STYLES = ("face", "core")


def get_hud_style() -> str:
    """Which centrepiece the HUD draws: the animated head, or the reactor core.

    Taste, not capability — both render in the same software painter and cost
    about the same. Defaults to the head because that is what MARK LIV shipped
    with; anyone who preferred the older look can switch back in ⚙ and the
    choice survives a restart.
    """
    v = str(load_api_keys().get("hud_style", "face")).strip().lower()
    return v if v in HUD_STYLES else "face"


def save_hud_style(style: str) -> None:
    s = str(style or "").strip().lower()
    _save_flag("hud_style", s if s in HUD_STYLES else "face")


def get_ui_scale() -> float:
    """How much larger than designed the whole interface is drawn, or 0 for
    'work it out from the screen'.

    Read once, before the window exists — Qt only takes a scale factor at
    start-up, so a change here applies on the next launch."""
    try:
        v = float(load_api_keys().get("ui_scale", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    return min(v, 3.0) if v >= 0.5 else 0.0


def save_ui_scale(scale: float) -> None:
    _save_flag("ui_scale", float(scale or 0))


# ── Live-session tuning ──────────────────────────────────────────────────────
# Everything here is optional and has a working default, so an untouched
# config behaves exactly like a configured one. Each value is also a way out:
# if a future model dislikes one of these, set it back and nothing else changes.

def get_thinking_enabled() -> bool:
    """Whether the Live model may spend tokens thinking before it answers.

    Off by default. A voice assistant is judged on how fast it starts talking,
    and the reasoning that actually needs deliberation in this app is delegated
    to the planning tools, which run on a separate non-Live model.
    """
    return bool(load_api_keys().get("thinking_enabled", False))


def save_thinking_enabled(enabled: bool) -> None:
    _save_flag("thinking_enabled", enabled)


def get_turn_tuning() -> dict:
    """How eagerly the server decides you have stopped speaking.

    OFF by default, and that default was earned. Cutting turns shorter looks
    like a free speed win and is not: proactive audio has to judge whether an
    utterance was even addressed to the assistant, and a turn clipped early
    gives it less to judge, so it stays quiet — and the reply to your first
    sentence only arrives once your second one has given it enough context.
    That reads as the assistant being a turn behind, which is far worse than
    the fraction of a second the tuning saves.

    Turn it on with "turn_tuning": {"enabled": true} if your own microphone and
    speaking pace suit it. `silence_ms` is the one that is felt: the pause the
    server sits through before accepting your turn is over.
    """
    cfg = load_api_keys().get("turn_tuning")
    cfg = cfg if isinstance(cfg, dict) else {}

    def _int(key, default, lo, hi):
        try:
            return max(lo, min(hi, int(cfg.get(key, default))))
        except (TypeError, ValueError):
            return default

    return {
        "enabled":    bool(cfg.get("enabled", False)),
        "silence_ms": _int("silence_ms", 550, 200, 3000),
        "prefix_ms":  _int("prefix_ms", 150, 0, 1000),
        # "high" = quicker to decide speech has ended.
        "end_sensitivity":   str(cfg.get("end_sensitivity", "high")).lower(),
        "start_sensitivity": str(cfg.get("start_sensitivity", "default")).lower(),
    }


@_locked
def save_turn_tuning(values: dict) -> None:
    ensure_config_dir()
    data = _read_for_write()
    cur = data.get("turn_tuning")
    cur = dict(cur) if isinstance(cur, dict) else {}
    cur.update(values or {})
    data["turn_tuning"] = cur
    _write_config(data)


def get_proactive_audio_enabled() -> bool:
    """Whether the model gets to decide an utterance was not aimed at it and
    stay quiet.

    On by default — it is what stops the assistant answering the room. But it
    is also the first thing to switch off if replies ever seem to arrive a turn
    late: what looks like lag is usually the model having judged your previous
    sentence as not addressed to it, and only changing its mind once the next
    one arrives.
    """
    return bool(load_api_keys().get("proactive_audio", True))


def save_proactive_audio_enabled(enabled: bool) -> None:
    _save_flag("proactive_audio", enabled)


MEDIA_RESOLUTIONS = ("default", "low", "medium", "high")


def get_media_resolution() -> str:
    """How finely the model tokenises the screenshots and camera frames it is
    sent. 'medium' keeps on-screen text readable at a fraction of the tokens a
    full-resolution frame costs; 'low' is cheaper still but starts losing small
    text, which is most of what screen captures are for."""
    v = str(load_api_keys().get("media_resolution", "medium")).strip().lower()
    return v if v in MEDIA_RESOLUTIONS else "medium"


def save_media_resolution(value: str) -> None:
    v = str(value or "").strip().lower()
    _save_flag("media_resolution", v if v in MEDIA_RESOLUTIONS else "medium")


@_locked
def _save_flag(key: str, value) -> None:
    """Read-modify-write one key without disturbing the rest of the config."""
    ensure_config_dir()
    data = _read_for_write()
    data[key] = bool(value) if isinstance(value, bool) else value
    _write_config(data)


def get_brief_enabled() -> bool:
    return load_api_keys().get("morning_brief_enabled", True)


@_locked
def save_brief_enabled(enabled: bool) -> None:
    ensure_config_dir()
    data = _read_for_write()
    data["morning_brief_enabled"] = enabled
    _write_config(data)


# ── Audio devices ────────────────────────────────────────────────────────────
# Stored as device NAMES, not sounddevice indices. Indices shift every time a
# USB device is plugged in or removed, so a saved index silently starts pointing
# at a different microphone. The empty string means "system default", which is
# both the factory setting and what an unresolvable saved device falls back to —
# so unplugging a headset degrades to the built-in speakers instead of crashing.

@_locked
def _patch_config(**fields) -> None:
    """Read-modify-write one or more keys in api_keys.json.

    Every setter in this file open-coded this. Collapsing it here means a new
    setting is one line, and there is one place where a corrupt config file is
    handled instead of nine."""
    ensure_config_dir()
    data = _read_for_write()
    pending = dict(fields)

    if "gemini_api_key" in pending:
        raw_gemini = pending.pop("gemini_api_key")
        from memory import credential_store

        if credential_store.parse_secret_ref(raw_gemini) is None:
            gkey = str(raw_gemini or "").strip()
            if gkey:
                credential_store.save_gemini_key(gkey)
                data.pop("local_only", None)
            else:
                had_legacy = bool(str(data.get("gemini_api_key") or "").strip())
                try:
                    credential_store.delete_gemini_key()
                except credential_store.CredentialStoreUnavailable:
                    if not had_legacy:
                        raise
            data.pop("gemini_api_key", None)

    if "providers" in pending and isinstance(pending["providers"], dict):
        p_block = dict(pending.pop("providers"))
        if isinstance(p_block.get("creds"), dict):
            existing_creds = (
                data.get("providers", {}).get("creds")
                if isinstance(data.get("providers"), dict)
                else None
            )
            p_block["creds"] = _sanitize_provider_creds_for_write(
                p_block["creds"], existing_creds
            )
        data["providers"] = p_block

    if "plugin_config" in pending and isinstance(pending["plugin_config"], dict):
        pc_block = dict(pending.pop("plugin_config"))
        existing_pc = data.get("plugin_config") if isinstance(data.get("plugin_config"), dict) else {}
        sanitized_pc: dict = {}
        for ns, ns_vals in pc_block.items():
            if isinstance(ns_vals, dict):
                sanitized_pc[ns] = _sanitize_plugin_ns_for_write(
                    str(ns), ns_vals, existing_pc.get(ns)
                )
            else:
                sanitized_pc[ns] = ns_vals
        data["plugin_config"] = sanitized_pc

    for k, v in pending.items():
        if _is_secret_key(k):
            stored = _store_secret_field(k, v, data.get(k), use_ref=True)
            if stored:
                data[k] = stored
            else:
                data.pop(k, None)
        else:
            data[k] = v

    _write_config(data)


def update_config(**fields) -> None:
    """Set one or more top-level settings, leaving every other one as it is.
    For code outside this file that used to rewrite api_keys.json itself."""
    _patch_config(**fields)


def get_input_device() -> str:
    """Microphone device name, or '' for the system default."""
    return (load_api_keys().get("input_device", "") or "").strip()


def save_input_device(name: str) -> None:
    _patch_config(input_device=(name or "").strip())


def get_output_device() -> str:
    """Speaker device name, or '' for the system default."""
    return (load_api_keys().get("output_device", "") or "").strip()


def save_output_device(name: str) -> None:
    _patch_config(output_device=(name or "").strip())


def get_plugin_enabled(plugin_name: str) -> bool:
    """Plugins are enabled by default the moment they're discovered (opt-out model)."""
    return load_api_keys().get("plugins_enabled", {}).get(plugin_name, True)


# ── Per-plugin settings ("tokens" / connection details) ───────────────────────
# Generic store so a plugin can declare its own config fields (PLUGIN_SETTINGS)
# and the settings UI renders + persists them WITHOUT any core edit — keeping the
# drop-in model intact. Values live under plugin_config[<namespace>][<key>].
# A namespace defaults to the plugin name, but a suite of plugins (e.g. the
# several printer plugins) can share ONE namespace.
def _migrate_plugin_secret(namespace: str, key: str, ref: dict) -> None:
    with _lock:
        data = _read_for_write()
        pc = data.get("plugin_config")
        if not isinstance(pc, dict):
            return
        ns_cfg = pc.get(namespace)
        if not isinstance(ns_cfg, dict):
            return
        if isinstance(ns_cfg.get(key), str) and ns_cfg[key].strip():
            ns_cfg[key] = ref
            _write_config(data)


def get_plugin_config(namespace: str) -> dict:
    """All stored values for a namespace (empty dict if none set yet)."""
    cfg = load_api_keys().get("plugin_config")
    val = cfg.get(namespace) if isinstance(cfg, dict) else None
    if not isinstance(val, dict):
        return {}
    out: dict = {}
    for k, v in val.items():
        if _is_secret_key(k):
            out[k] = _resolve_secret_field(
                _plugin_secret_id(namespace, k),
                v,
                on_migrate=lambda ref, _ns=namespace, _k=k: _migrate_plugin_secret(_ns, _k, ref),
            )
        else:
            out[k] = v
    return out


def get_plugin_setting(namespace: str, key: str, default=None):
    """A single value from a namespace, or `default` if unset."""
    return get_plugin_config(namespace).get(key, default)


@_locked
def save_plugin_config(namespace: str, values: dict) -> None:
    """Merge `values` into a namespace's stored config (read-modify-write, like
    every other helper here). Only the provided keys are touched."""
    ensure_config_dir()
    data = _read_for_write()
    pc = data.get("plugin_config")
    if not isinstance(pc, dict):
        pc = {}
    cur = pc.get(namespace)
    if not isinstance(cur, dict):
        cur = {}
    merged = dict(cur)
    merged.update(values or {})
    pc[namespace] = _sanitize_plugin_ns_for_write(namespace, merged, cur)
    data["plugin_config"] = pc
    _write_config(data)


@_locked
def save_plugin_enabled(plugin_name: str, enabled: bool) -> None:
    ensure_config_dir()
    data = _read_for_write()
    plugins_cfg = data.get("plugins_enabled")
    if not isinstance(plugins_cfg, dict):
        plugins_cfg = {}
    plugins_cfg[plugin_name] = enabled
    data["plugins_enabled"] = plugins_cfg
    _write_config(data)

# ── Providers: local STT/LLM/TTS and non-Gemini APIs ─────────────────────────
# Stored here rather than in a file of their own, for the same reason plugin
# settings are: one config to back up, one already in .gitignore, one place a
# user knows to look.
#
# The important property is what happens when nobody has configured anything.
# `get_provider_mode()` returns "gemini" on a config that has never heard of
# providers, and every caller checks that BEFORE importing the providers
# package — so an ordinary install never executes a line of it. This is the
# whole basis of the claim that the folder costs nothing when unused.
PROVIDER_MODE_GEMINI = "gemini"      # the conversation runs on the Live API
PROVIDER_MODE_PIPELINE = "pipeline"  # STT -> LLM -> TTS, assembled from providers


def _upgrade_ids(block: dict) -> dict:
    """Give every catalog service its own id and its own stored settings.

    One provider serves Ollama, OpenAI, MiniMax and a dozen more, and they used
    to share ONE block of settings under "openai_compat" — so the fallback and
    ② THINKING could not be two different services, picking one overwrote the
    other's address, and an API key typed for MiniMax was sent to OpenAI after
    a switch. Each service is now "<provider>@<entry>", e.g.
    "openai_compat@minimax", with settings of its own.

    An older config is converted as it is read, not by a migration step: the
    shared block moves to the service its "catalog" key names (its key belonged
    to that service and nothing else), and every reference to it follows.
    Writing the providers block back persists it; until then nothing on disk
    changes."""
    creds = block.get("creds")
    if not isinstance(creds, dict):
        return block
    creds = dict(creds)
    moved: dict = {}
    for pid, cfg in list(creds.items()):
        if "@" in pid or not isinstance(cfg, dict):
            continue
        eid = str(cfg.get("catalog") or "").strip()
        if not eid:
            continue
        new = f"{pid}@{eid}"
        creds.setdefault(new, dict(cfg))
        del creds[pid]
        moved[pid] = new
    if not moved:
        return block
    block = dict(block)
    block["creds"] = creds
    pipe = block.get("pipeline")
    if isinstance(pipe, dict):
        block["pipeline"] = {k: moved.get(v, v) for k, v in pipe.items()}
    for key in ("sidecar", "coding"):
        if block.get(key) in moved:
            block[key] = moved[block[key]]
    return block


def _get_providers_raw() -> dict:
    val = load_api_keys().get("providers")
    return _upgrade_ids(dict(val)) if isinstance(val, dict) else {}


def _migrate_provider_secret(provider_id: str, key: str, ref: dict) -> None:
    with _lock:
        data = _read_for_write()
        block = data.get("providers")
        if not isinstance(block, dict):
            return
        creds = block.get("creds")
        if not isinstance(creds, dict):
            return
        cfg = creds.get(provider_id)
        if not isinstance(cfg, dict):
            return
        if isinstance(cfg.get(key), str) and cfg[key].strip():
            cfg[key] = ref
            _write_config(data)


def _resolve_provider_cfg(provider_id: str, cfg: dict) -> dict:
    out: dict = {}
    for k, v in cfg.items():
        if _is_secret_key(k):
            out[k] = _resolve_secret_field(
                _provider_secret_id(provider_id, k),
                v,
                on_migrate=lambda ref, _pid=provider_id, _k=k: _migrate_provider_secret(
                    _pid, _k, ref
                ),
            )
        else:
            out[k] = v
    return out


def get_providers() -> dict:
    """The whole providers block, with credential references resolved."""
    block = _get_providers_raw()
    creds = block.get("creds")
    if isinstance(creds, dict):
        block["creds"] = {
            pid: _resolve_provider_cfg(pid, cfg) if isinstance(cfg, dict) else cfg
            for pid, cfg in creds.items()
        }
    return block


def get_provider_mode() -> str:
    """Which engine holds the conversation. Defaults to Gemini Live."""
    mode = str(_get_providers_raw().get("mode", "") or "").strip().lower()
    return mode if mode in (PROVIDER_MODE_GEMINI, PROVIDER_MODE_PIPELINE) \
        else PROVIDER_MODE_GEMINI


def get_provider_config(provider_id: str) -> dict:
    """Stored settings for one provider — address, model, key, limits."""
    creds = _get_providers_raw().get("creds")
    val = creds.get(provider_id) if isinstance(creds, dict) else None
    return _resolve_provider_cfg(provider_id, val) if isinstance(val, dict) else {}


def get_pipeline() -> dict:
    """Which provider fills each slot of the pipeline engine."""
    val = _get_providers_raw().get("pipeline")
    return dict(val) if isinstance(val, dict) else {}


def get_sidecar_provider() -> str:
    """The provider id used for one-shot calls when Gemini cannot answer them,
    or '' for none.

    Separate from the conversation on purpose: these are different jobs with
    different failure modes. Gemini's Live quota and its text quota are separate
    pools, and on the free tier it is the text pool that runs out first — which
    kills document reading, screen reasoning and code generation while the
    conversation carries on. Being able to point only those at another model,
    without changing the voice at all, is the cheapest useful thing this whole
    folder does.
    """
    return str(_get_providers_raw().get("sidecar", "") or "").strip()


@_locked
def _patch_providers(**fields) -> None:
    p = _get_providers_raw()
    p.update(fields)
    _patch_config(providers=p)


def save_provider_mode(mode: str) -> None:
    mode = (mode or "").strip().lower()
    _patch_providers(mode=mode if mode in (PROVIDER_MODE_GEMINI,
                                           PROVIDER_MODE_PIPELINE)
                     else PROVIDER_MODE_GEMINI)


@_locked
def save_provider_config(provider_id: str, values: dict) -> None:
    """Merge settings for one provider. Only the given keys are touched, so the
    panel can save one field without reading and rewriting the rest."""
    ensure_config_dir()
    data = _read_for_write()
    block = data.get("providers")
    block = _upgrade_ids(dict(block)) if isinstance(block, dict) else {}
    creds = block.get("creds")
    creds = dict(creds) if isinstance(creds, dict) else {}
    existing = creds.get(provider_id)
    existing = dict(existing) if isinstance(existing, dict) else {}
    merged = dict(existing)
    merged.update(values or {})
    creds[provider_id] = _sanitize_provider_creds_for_write(
        {provider_id: merged}, {provider_id: existing}
    )[provider_id]
    block["creds"] = creds
    data["providers"] = block
    _write_config(data)


@_locked
def save_pipeline(stt: str = None, llm: str = None, tts: str = None) -> None:
    """Set one or more pipeline slots. `None` leaves a slot alone; '' clears it."""
    p = _get_providers_raw()
    pipe = p.get("pipeline")
    pipe = dict(pipe) if isinstance(pipe, dict) else {}
    for slot, value in (("stt", stt), ("llm", llm), ("tts", tts)):
        if value is not None:
            pipe[slot] = str(value).strip()
    p["pipeline"] = pipe
    _patch_config(providers=p)


def save_sidecar_provider(provider_id: str) -> None:
    _patch_providers(sidecar=(provider_id or "").strip())


def get_coding_provider() -> str:
    """The service dev_agent writes code with, or '' to use whatever answers
    background requests.

    Its own setting because coding is the one background job where the model
    matters most and the request is longest — somebody may well want a strong
    coding model there and something cheap and fast for reading the screen."""
    return str(_get_providers_raw().get("coding", "") or "").strip()


def save_coding_provider(provider_id: str) -> None:
    _patch_providers(coding=(provider_id or "").strip())


def get_sidecar_first() -> bool:
    """Whether the side-call provider is tried BEFORE Gemini rather than after.

    Off by default: measured in core/gemini.py, the Gemini rungs answer a short
    request in well under a second, and a local model on ordinary hardware does
    not. On means something specific — 'I would rather this stayed on my
    machine, and I accept it being slower' — so it is a choice someone makes,
    never a default they inherit.
    """
    return bool(_get_providers_raw().get("sidecar_first", False))


def save_sidecar_first(enabled: bool) -> None:
    _patch_providers(sidecar_first=bool(enabled))


@_locked
def save_providers(mode: str = None, pipeline: dict = None, creds: dict = None,
                   sidecar: str = None, sidecar_first: bool = None,
                   coding: str = None) -> None:
    """Write the whole providers block in ONE file write.

    The settings panel used to call five setters in a row — one per provider's
    credentials, then the mode, then the pipeline, then the two side-call
    keys — and every one of them was a separate read-modify-write of
    api_keys.json. Five reads and five writes of the same file, in sequence,
    while the panel's own APPLY was already triggering a reconnect that makes
    main.py write to it too. Any interleaving loses whichever key was written
    first, and the one people noticed was the mode: set MY OWN SETUP, save,
    reopen, and it says Gemini again.

    `None` leaves a section alone; `creds` is merged per provider so a panel
    that only edited one does not have to send the others.
    """
    ensure_config_dir()
    data = _read_for_write()

    block = data.get("providers")
    block = _upgrade_ids(dict(block)) if isinstance(block, dict) else {}

    if mode is not None:
        m = str(mode).strip().lower()
        block["mode"] = m if m in (PROVIDER_MODE_GEMINI,
                                   PROVIDER_MODE_PIPELINE) else PROVIDER_MODE_GEMINI
    if pipeline is not None:
        current = block.get("pipeline")
        current = dict(current) if isinstance(current, dict) else {}
        current.update({k: str(v or "").strip() for k, v in pipeline.items()})
        block["pipeline"] = current
    if creds is not None:
        store = block.get("creds")
        store = dict(store) if isinstance(store, dict) else {}
        for pid, values in (creds or {}).items():
            existing = store.get(pid)
            existing = dict(existing) if isinstance(existing, dict) else {}
            merged = dict(existing)
            merged.update(values or {})
            store[pid] = _sanitize_provider_creds_for_write(
                {pid: merged}, {pid: existing}
            )[pid]
        block["creds"] = store
    if sidecar is not None:
        block["sidecar"] = str(sidecar or "").strip()
    if sidecar_first is not None:
        block["sidecar_first"] = bool(sidecar_first)
    if coding is not None:
        block["coding"] = str(coding or "").strip()

    data["providers"] = block
    _write_config(data)


@_locked
def migrate_plaintext_credentials() -> dict[str, Any]:
    """Migrate any legacy plaintext credentials in api_keys.json to the vault.

    Never deletes a plaintext value unless the vault write and read-back
    verification succeed. Returns only field names and counts, never secrets.
    """
    from memory import credential_store

    report: dict[str, Any] = {
        "migrated": [],
        "already_migrated": [],
        "failed": [],
        "backend": credential_store.backend_name(),
    }
    if not CONFIG_FILE.exists():
        return report

    data = _read_for_write()
    changed = False

    def _try_move(label: str, secret_id: str, raw: Any, setter) -> None:
        nonlocal changed
        if credential_store.parse_secret_ref(raw) is not None:
            report["already_migrated"].append(label)
            return
        if not isinstance(raw, str) or not raw.strip():
            return
        try:
            credential_store.save_secret(secret_id, raw.strip())
            setter(credential_store.make_secret_ref(secret_id))
            changed = True
            report["migrated"].append(label)
        except credential_store.CredentialStoreUnavailable:
            report["failed"].append(label)

    # Top-level Gemini key (removed rather than replaced with $secret_ref)
    raw_gemini = data.get("gemini_api_key")
    if isinstance(raw_gemini, str) and raw_gemini.strip():
        try:
            credential_store.save_gemini_key(raw_gemini.strip())
            data.pop("gemini_api_key", None)
            changed = True
            report["migrated"].append("gemini_api_key")
        except credential_store.CredentialStoreUnavailable:
            report["failed"].append("gemini_api_key")

    for top_key in list(data.keys()):
        if top_key == "gemini_api_key":
            continue
        if _is_secret_key(top_key):
            _try_move(
                top_key,
                top_key,
                data.get(top_key),
                lambda ref, _k=top_key: data.__setitem__(_k, ref),
            )

    prov = data.get("providers")
    if isinstance(prov, dict) and isinstance(prov.get("creds"), dict):
        for pid, cfg in prov["creds"].items():
            if not isinstance(cfg, dict):
                continue
            for k in list(cfg.keys()):
                if _is_secret_key(k):
                    _try_move(
                        f"providers.creds.{pid}.{k}",
                        _provider_secret_id(str(pid), k),
                        cfg.get(k),
                        lambda ref, _cfg=cfg, _k=k: _cfg.__setitem__(_k, ref),
                    )

    pc = data.get("plugin_config")
    if isinstance(pc, dict):
        for ns, ns_cfg in pc.items():
            if not isinstance(ns_cfg, dict):
                continue
            for k in list(ns_cfg.keys()):
                if _is_secret_key(k):
                    _try_move(
                        f"plugin_config.{ns}.{k}",
                        _plugin_secret_id(str(ns), k),
                        ns_cfg.get(k),
                        lambda ref, _cfg=ns_cfg, _k=k: _cfg.__setitem__(_k, ref),
                    )

    if changed:
        _write_config(data)
    return report

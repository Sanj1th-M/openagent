# Remaining CodeQL remediation audit

Branch: `security/codeql-remaining-alerts`  
Baseline: `2f79169482441eec5fce4d5252909bb06b478cd3`

This audit records implementation status, not alert dismissal. GitHub's
default-branch alerts remain open until this branch is merged and `main` is
analysed again.

| Alerts | Rule | Status | Evidence / remaining work |
| --- | --- | --- | --- |
| 22-25 | path-injection, insecure-protocol | Fixed on `main` | Merged prior to this branch via PR #2 and PR #3. |
| 19-21 | incomplete URL substring sanitization | Fixed on PR branch | `providers/llm_openai.py`, `providers/stt_whisper.py`, `providers/tts_elevenlabs.py`, and `core/llm_client.py` validate HTTP/HTTPS URLs with `urlsplit`, block link-local/unspecified/multicast/metadata hosts, handle IPv6 loopback/LAN detection, withhold API keys on HTTP, disable redirects, and require 2xx responses. |
| 18 | reflected XSS | Fixed on PR branch | `dashboard/server.py` `/auto-login` validates against generated `_pending_keys` and serializes inline-script values via `_script_json()` with HTML-breakout escaping. |
| 17 | URL redirection | Fixed on PR branch | `_https_redirect_target()` pins destination authority locally (including bracketed IPv6 hosts) while preserving path/query. |
| 16 | bind socket to all interfaces | Fixed on PR branch | Dashboard binds `127.0.0.1` by default across listeners and port probes; LAN exposure requires explicit `"dashboard_bind_host": "0.0.0.0"`. |
| 15 | stack-trace exposure | Fixed on PR branch | `/api/upload` returns generic `{"error": "Upload failed"}` while logging only the exception class locally. |
| 7-14, 6, 3 | clear-text logging | Fixed on PR branch | `core/gemini.py` (recoverable key read, no model/raw error logs), `main.py` (no device names/raw exceptions, `dev_agent` args omitted from tool logs), `core/audio_devices.py`, `actions/reminder.py`, and `actions/dev_agent.py` (comprehensive credential/URI scrubber, pre-truncation redaction, scrubbed job snapshots). |
| 2 | clear-text logging (`providers/_tests.py`) | Fixed on PR branch | `providers/_tests.py` logs only non-secret `base_url` failure detail rather than the full provider config dict. |
| 4, 5 | clear-text logging (`core/cloud.py`) | Separated operator output | Intentional `--init` / `--pair-code` / `--rotate` CLI pairing output uses `_write_operator_output()` writing directly to `sys.__stdout__` (bypassing `HeadlessPlayer`'s `_Tee` log buffer), separated from generic `_say()` logging which redacts pairing URIs, bearer tokens, and labeled secrets. Note: CodeQL PR check still flags `_write_operator_output()` on the intentional `--pair-code` CLI output and `_say()` via `load_api_keys()` -> `get_assistant_name()` (false positive on non-secret `assistant_name`); neither is suppressed. |
| 1 | clear-text storage | Fixed on PR branch | `memory/credential_store.py` + `memory/config_manager.py` store Gemini, provider `api_key`s, plugin secrets (`bot_token`, `pair_code`, etc.), and `elevenlabs_api_key` via Windows Credential Manager (`win32cred`), macOS Keychain / Linux Secret Service (`keyring`), or env/`CREDENTIALS_DIRECTORY`/external provider on headless Linux—never silently falling back to plaintext JSON. All direct readers/writers across `main.py`, `gui/`, `dashboard/`, `core/`, and `actions/` are centralized, with verified read-back migration and POSIX `0o700`/`0o600` permission hardening. |

## Verification

- Local checks executed after final changes on Windows:
  - `python -m pytest tests/ -q`: 62 passed, 6 skipped (POSIX permission and symlink tests skipped on Windows).
  - `python -m providers._tests`: 100 passed, 0 failed.
  - `ruff check tests/ setup.py` and `ruff format --check tests/ setup.py`: passed.
  - `mypy tests/`: passed.
  - `bandit -ll -r actions core gui memory plugins main.py ui.py setup.py tests`: 0 Medium/High issues.
  - `pip-audit -r requirements.txt`: no known vulnerabilities found.
- GitHub Actions CI and CodeQL PR checks are tracked on draft PR #6 (`security/codeql-remaining-alerts` -> `main`). On commit `0c7c580`, all CI/CD and Security jobs passed across Ubuntu (`3.11`, `3.12`), Windows (`3.11`, `3.12`), and macOS (`3.11`), including Ruff, Mypy, Bandit, pip-audit, Packaging, and `CodeQL - Static analysis`; the CodeQL PR gate reported only the `core/cloud.py` `_write_operator_output` (`--pair-code` secret), `_say` (`load_api_keys().get("assistant_name")` false positive), and `_Tee.write` findings.

## Recovery and migration

If a writable secure credential backend is unavailable (such as headless Linux
without an external provider or environment configuration), saving new secrets
fails closed instead of writing plaintext to `config/api_keys.json`. Existing
plaintext entries remain readable and intact until a write + read-back
verification succeeds, at which point Gemini keys are removed from JSON and
provider/plugin/TTS secrets are replaced atomically with `{"$secret_ref": ...}`
references. Back up `config/api_keys.json` before upgrading if a legacy
credential must be recovered.

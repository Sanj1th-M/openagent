# Configuration Guide

This document details the configuration architecture, options, and credential management in **openagent**.

---

## 1. Configuration Architecture

All user settings, API keys, provider configurations, and feature toggles are stored locally in:
```
config/api_keys.json
```

### Key Architectural Tenets:
1. **Single Source of Truth**: All configurations reside in one JSON file, making backup, export, and migration straightforward.
2. **Atomic Writes**: Managed by `memory/config_manager.py`. Configuration updates are written to a same-directory temporary file and atomically replaced using `os.replace`. On POSIX, the config directory and file are restricted to the current user. This eliminates race conditions and file corruptions across concurrent threads.
3. **Automatic Backup**: If `config/api_keys.json` is corrupted (e.g. by manual editing errors), it is automatically preserved as `config/api_keys.json.broken-<timestamp>` rather than overwritten, protecting user keys.
4. **Git Protection**: `config/api_keys.json` is strictly ignored by version control in `.gitignore`. **Never commit actual API keys or secrets.**

---

## 2. Configuration Schema & Options

A representative `config/api_keys.json` configuration file:

```json
{
  "gemini_api_key": "YOUR_GEMINI_API_KEY",
  "local_only": false,
  "assistant_name": "Jarvis",
  "wake_word_enabled": false,
  "wake_word": "hey jarvis",
  "audio_input_device": null,
  "audio_output_device": null,
  "providers": {
    "mode": "pipeline",
    "pipeline": {
      "llm": "openai_compat@ollama",
      "stt": "whisper",
      "tts": "piper"
    },
    "creds": {
      "openai_compat@ollama": {
        "catalog": "ollama",
        "base_url": "http://localhost:11434/v1",
        "api_key": "",
        "model": "llama3.2:latest"
      },
      "whisper": {
        "model": "base",
        "device": "cpu"
      },
      "piper": {
        "voice": "en_US-lessac-medium"
      },
      "elevenlabs": {
        "api_key": "YOUR_ELEVENLABS_KEY",
        "voice_id": "21m00Tcm4TlvDq8ikWAM"
      }
    },
    "sidecar": "openai_compat@ollama",
    "sidecar_first": false,
    "coding": "openai_compat@ollama"
  },
  "dashboard_https": false,
  "theme": "arc_reactor_cyan"
}
```

---

## 3. Configuration Details

### A. Primary LLM: Google Gemini
- **`gemini_api_key`** *(string)*: API key for Google Gemini Live and REST multimodal endpoints.
- **`local_only`** *(boolean)*: When `true`, disables outbound Google Gemini requests and forces the modular provider pipeline (`providers/engine_pipeline.py`).

### B. Assistant Personality & Identity
- **`assistant_name`** *(string)*: Customizable display name for the assistant (default: `"Jarvis"`). The assistant branding is **openagent**, but users can personalize their assistant's call sign.
- **`wake_word_enabled`** *(boolean)*: Toggle local wake-word listening.
- **`wake_word`** *(string)*: Custom wake phrase (default: `"hey jarvis"`).

### C. Audio Device Settings
- **`audio_input_device`** *(integer | null)*: Index or name of the microphone device enumerated by `sounddevice`. `null` selects the OS default.
- **`audio_output_device`** *(integer | null)*: Index or name of the audio speaker device. `null` selects the OS default.

### D. Provider Configuration (`memory/config_manager.py`)
openagent supports modular local and third-party models alongside Gemini Live:
- **`providers.mode`** *(string)*: Selects the active conversation engine: `"gemini"` (default: Live API) or `"pipeline"` (modular STT -> LLM -> TTS pipeline).
- **`providers.pipeline`** *(object)*: Active provider selections for each pipeline slot:
  - `llm`: Selected LLM provider (e.g. `"openai_compat@ollama"`, `"openai_compat@groq"`).
  - `stt`: Speech-to-Text provider (e.g. `"whisper"`).
  - `tts`: Text-to-Speech provider (e.g. `"piper"`, `"system_tts"`, `"elevenlabs"`).
- **`providers.creds`** *(object)*: Stored credentials, endpoints, models, and options keyed by provider identifier (e.g. `"openai_compat@ollama"`, `"whisper"`, `"piper"`).
- **`providers.sidecar`** *(string)*: Provider ID used for one-shot background reasoning/tools when Gemini quota is exhausted.
- **`providers.sidecar_first`** *(boolean)*: When `true`, routes background tool calls to the sidecar model before Gemini.
- **`providers.coding`** *(string)*: Dedicated provider ID used by the autonomous coding agent (`actions/dev_agent.py`).

### E. Remote Dashboard (`dashboard/server.py`)
- **`dashboard_https`** *(boolean)*: When `true`, enables TLS on `SECURE_PORT` 8001 with auto-generated self-signed certificates and AES-256-GCM command payload encryption, while running an HTTP-to-HTTPS redirector on port 8000. When `false` (default) or if certificate generation fails, the dashboard serves plain HTTP on port 8000.
- **Pairing Authentication**: An 8-character ephemeral pairing code is generated per session and strengthened with PBKDF2 (200,000 iterations). Authentication tokens expire automatically, and failed login attempts are rate-limited.

---

## 4. Environment Variables

While `config/api_keys.json` is the standard store, openagent supports the following environment overrides:

| Variable | Description | Default |
|---|---|---|
| `GEMINI_API_KEY` | Overrides the Gemini API key in headless or containerized environments. | *None* |
| `QT_QPA_PLATFORM` | Configures the PyQt6 graphics platform (e.g. `offscreen` for headless CI runs). | System Default |
| `PYTHONPATH` | Python import resolution path (typically set to `.`). | Current Directory |

---

## 5. Safe Configuration Management

To modify settings programmatically from tools or actions, always use the public APIs in `memory/config_manager.py`:

```python
from memory import config_manager

# Read settings
keys = config_manager.load_api_keys()
gemini_key = keys.get("gemini_api_key")

# Save settings atomically
config_manager.save_api_keys("AIzaSy...")
```
*Never edit `config/api_keys.json` using raw file open/write statements in tools.*

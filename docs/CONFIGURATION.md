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
2. **Atomic Writes**: Managed by `memory/config_manager.py`. Configuration updates are written to `config/api_keys.json.tmp` and atomically replaced using `os.replace`. This eliminates race conditions and file corruptions across concurrent threads.
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
    "active_llm": "gemini",
    "active_stt": "whisper_local",
    "active_tts": "system",
    "llm_openai": {
      "base_url": "http://localhost:11434/v1",
      "api_key": "optional-key-for-local-server",
      "model": "llama3"
    },
    "tts_elevenlabs": {
      "api_key": "YOUR_ELEVENLABS_KEY",
      "voice_id": "21m00Tcm4TlvDq8ikWAM"
    }
  },
  "dashboard": {
    "enabled": false,
    "pairing_code": "A3K9M2P4",
    "port": 8000,
    "secure_port": 8001
  },
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

### D. Provider Registry (`providers/`)
OpenAgent allows plug-and-play swapping of model providers via `providers/registry.py`:
- **`active_llm`**: Selected LLM provider (`"gemini"`, `"llm_openai"`, or custom catalog entries).
- **`active_stt`**: Selected Speech-to-Text provider (`"gemini_live"`, `"stt_whisper"`).
- **`active_tts`**: Selected Text-to-Speech provider (`"gemini_live"`, `"tts_piper"`, `"tts_elevenlabs"`, `"tts_system"`).

### E. Remote Dashboard (`dashboard/`)
- **`dashboard.enabled`** *(boolean)*: Enables the local FastAPI web server.
- **`dashboard.port`** *(integer, default 8000)*: Plaintext HTTP port that automatically redirects to HTTPS.
- **`dashboard.secure_port`** *(integer, default 8001)*: Secure HTTPS listener with auto-generated self-signed TLS certificates and AES-256-GCM payload encryption.
- **`dashboard.pairing_code`** *(string)*: High-entropy pairing token used with PBKDF2 (200,000 iterations) to authenticate phone/remote sessions.

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

# API Reference & Interfaces

This document provides technical documentation of the internal APIs, provider interfaces, plugin architectures, and dashboard HTTP/WebSocket endpoints in **openagent**.

---

## 1. Provider Interfaces (`providers/base.py`)

All pluggable AI providers adhere to protocols and declarations in `providers/base.py`.

### Provider Declaration Schema
Every provider module declares a `PROVIDER` metadata dictionary:

```python
PROVIDER = {
    "id": "openai_compat",              # Unique, stable, stored in config
    "kind": "llm",                       # Options: "llm" | "stt" | "tts" | "engine"
    "label": "OpenAI-compatible",        # Display label for GUI settings
    "hint": "Ollama · LM Studio · Groq · DeepSeek · OpenRouter · vLLM",
    "requires": ("httpx",),              # Checked via find_spec, not imported at boot
    "install": "httpx",                  # Package installed by one-click button
    "needs": (
        Field("base_url", "Base URL", default="http://localhost:11434/v1"),
        Field("model", "Model"),
        Field("api_key", "API key", kind="secret", required=False),
    ),
    "caps": {"tools": True, "tool_format": "openai", "max_tools": 12},
    "factory": _make,                    # Called once, lazily, on first use
}
```

### Core Provider Protocols

#### 1. `LLM` (`providers/base.py`)
```python
class LLM(Protocol):
    """Text in, text out, with tools."""
    caps: dict

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        stream: bool = True,
    ) -> AsyncIterator[dict]:
        """Yields {"text": str} and/or {"tool_calls": [FunctionCall, ...]}."""
        ...
```

#### 2. `STT` (`providers/base.py`)
```python
class STT(Protocol):
    """Audio in, text out."""
    caps: dict

    async def transcribe(
        self,
        pcm: bytes,
        sample_rate: int = 16000,
        language: str = "",
    ) -> str:
        """Transcribe raw PCM audio to text."""
        ...
```

#### 3. `TTS` (`providers/base.py`)
```python
class TTS(Protocol):
    """Text in, 24 kHz mono PCM out."""
    caps: dict

    async def speak(
        self,
        text: str,
        voice: str = "",
    ) -> AsyncIterator[bytes]:
        """Synthesize text into chunks of 24 kHz mono PCM audio."""
        ...
```

---

## 2. Plugin & Action System

### Plugin Specification (`plugins/<plugin_name>.py`)
Plugins are single-file extensions dropped into `plugins/`. They are loaded dynamically without modifying application configuration:

```python
PLUGIN = {
    "name": "example_tool",
    "description": "Performs a specific task and returns a brief spoken summary.",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "Input parameter for the operation"
            }
        },
        "required": ["query"]
    }
}

def run(parameters: dict, player=None, session_memory=None) -> str:
    """Execute plugin work on a worker thread.

    Args:
        parameters: Parsed argument dictionary from the LLM.
        player: JarvisUI instance providing thread-safe GUI calls.
        session_memory: Dict containing per-session scratch memory.

    Returns:
        A short plain-text sentence that the assistant speaks aloud.
    """
    query = parameters.get("query", "")
    if player:
        player.write_log(f"Running example tool for: {query}")
    return f"Completed execution for {query}."
```

### Thread-Safe UI Bridge (`player` / `JarvisUI`)
Worker threads and plugins **must never create or modify Qt widgets directly**. Instead, invoke thread-safe methods on `player`:

| Method Signature | UI Effect |
|---|---|
| `player.write_log(text: str)` | Appends a timestamped line to the HUD log panel. |
| `player.show_content(title: str, text: str)` | Displays formatted text or report in the lower content panel. |
| `player.show_quiz(topic: str, questions: list[dict], grade=None)` | Renders an interactive quiz dialog on the Qt main thread. |
| `player.show_review(title: str, summary: str, findings: list)` | Displays a structured review view. |
| `player.show_video(path_or_url: str, title: str)` | Plays a video file or stream in the HUD media viewport. |

---

## 3. Remote Dashboard API (`dashboard/server.py`)

The local dashboard runs on `PORT 8000` (HTTP redirect) and `SECURE_PORT 8001` (HTTPS + TLS).

### Security & Pairing Flow
1. User pairs via QR code or by entering the 8-character pairing code.
2. The pairing code is processed with PBKDF2 (200,000 rounds) to generate a symmetric session key.
3. Payloads sent over WebSockets or HTTP are encrypted using AES-256-GCM.

### Endpoints

#### `GET /`
- Returns: `login.html` (if unauthenticated) or `app.html` (if authenticated).

#### `POST /auth/pair`
- Authenticates a remote client with the pairing code.
- **Request Body**: `{"code": "A3K9M2P4"}`
- **Response**: `{"status": "ok", "token": "<session_token>"}`

#### `GET /api/status`
- Returns assistant status, active model, and memory state.
- **Authentication**: Bearer session token.
- **Response**:
  ```json
  {
    "status": "listening",
    "assistant_name": "Jarvis",
    "active_provider": "gemini",
    "audio_level": 0.04
  }
  ```

#### `POST /api/command`
- Ingests a text instruction from the remote user.
- **Request Body**: `{"command": "Search the web for local weather"}`
- **Response**: `{"status": "dispatched"}`

#### `POST /api/upload`
- Accepts file uploads up to 500MB, stored in `uploads/` or `Downloads/JARVIS Uploads`.
- **Request Body**: `multipart/form-data` with file payload.

#### `WebSocket /ws/stream`
- Real-time bidirectional streaming for metrics, activity logs, and remote control events.

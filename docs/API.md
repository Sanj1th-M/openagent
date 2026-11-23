# API Reference & Interfaces

This document provides technical documentation of the internal APIs, provider interfaces, plugin architectures, and dashboard HTTP/WebSocket endpoints in **openagent**.

---

## 1. Provider Interfaces (`providers/base.py`)

All pluggable AI providers inherit from base structures in `providers/base.py`.

### Provider Declaration Schema
Every provider module declares a `PROVIDER` metadata dictionary:

```python
PROVIDER = {
    "id": "provider_identifier",
    "name": "Human-Readable Provider Name",
    "kind": "llm",  # Options: "llm" | "stt" | "tts" | "engine"
    "description": "Short explanation of capabilities",
    "fields": [
        Field(key="api_key", label="API Key", secret=True),
        Field(key="endpoint", label="Base URL", default="http://localhost:11434/v1")
    ],
    "caps": {"streaming": True, "tools": True}
}
```

### Core Provider Classes

#### 1. `BaseLLM` (`providers/base.py`)
```python
class BaseLLM(abc.ABC):
    @abc.abstractmethod
    async def chat(
        self,
        messages: list[Msg],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> Msg:
        """Execute a chat completion turn."""
        ...

    @abc.abstractmethod
    async def stream(
        self,
        messages: list[Msg],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.7
    ) -> AsyncIterator[str | FunctionCall]:
        """Stream response chunks or tool calls."""
        ...
```

#### 2. `BaseSTT` (`providers/base.py`)
```python
class BaseSTT(abc.ABC):
    @abc.abstractmethod
    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> str:
        """Convert raw PCM audio bytes to transcript text."""
        ...
```

#### 3. `BaseTTS` (`providers/base.py`)
```python
class BaseTTS(abc.ABC):
    @abc.abstractmethod
    async def synthesize(self, text: str, voice: Optional[str] = None) -> AsyncIterator[bytes]:
        """Synthesize text into raw PCM audio chunks."""
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

# API Reference & Interfaces

This document provides technical documentation of the internal APIs, provider interfaces, plugin architectures, and dashboard HTTP/WebSocket endpoints in **openagent**.

---

## 1. Provider Interfaces (`providers/base.py`)

All pluggable AI providers adhere to protocols and declarations in `providers/base.py`.

### Provider Declaration Schema
Every provider module declares a `PROVIDER` metadata dictionary and an `available()` function:

```python
from importlib.util import find_spec

PROVIDER = {
    "id": "openai_compat",              # Unique, stable, stored in config
    "kind": "llm",                       # Options: "llm" | "stt" | "tts" | "engine"
    "label": "OpenAI-compatible",        # Display label for GUI settings
    "hint": "Ollama · LM Studio · Groq · DeepSeek · OpenRouter · vLLM",
    "requires": ("httpx",),              # Dependencies checked via find_spec
    "install": "httpx",                  # Package installed by one-click button
    "needs": (
        Field("base_url", "Base URL", default="http://localhost:11434/v1"),
        Field("model", "Model"),
        Field("api_key", "API key", kind="secret", required=False),
    ),
    "caps": {"tools": True, "tool_format": "openai", "max_tools": 12},
    "factory": _make,                    # Called once, lazily, on first use
}

def available() -> bool:
    """True when this provider can be built.
    
    The registry does not inspect PROVIDER['requires'] automatically; it calls
    available() (defaulting to True if omitted). Using find_spec verifies
    dependencies without incurring heavy import latency or startup crashes.
    """
    return all(find_spec(pkg) is not None for pkg in PROVIDER["requires"])
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

The dashboard listens on port **8000** by default. When `dashboard_https` is set to `true` and certificate setup succeeds, it serves HTTPS on port **8001**. A GET request to port 8000 is redirected to `https://<server_lan_ip>:8001` using the server's detected LAN IP rather than the request host; requests through `localhost` or a tunnel may redirect to an unreachable address, so those clients and API clients (including login POST requests) should connect directly to HTTPS on port 8001. If HTTPS is disabled (the default) or certificate setup fails, the dashboard serves plain HTTP on port **8000**.

### Authentication and transport

- The desktop **Remote Control** UI generates a short-lived pairing PIN. A fresh `DashboardServer` has no pending PIN until that UI action creates one.
- `POST /login` exchanges the PIN for a bearer session token.
- Authenticated HTTP endpoints require `Authorization: Bearer <session_token>`, except where an endpoint explicitly uses a device-token or query-token mechanism.
- The browser derives an AES-256-GCM command key with PBKDF2 when Web Crypto is available. If key derivation or encryption is unavailable, the browser sends the command as a `text` field instead. HTTPS protects either request in transit only when the server is actually serving HTTPS. Plain HTTP does not provide TLS protection.
- Dashboard responses sent with `send_json` rely on HTTPS transport when HTTPS is active; they are not wrapped in the command AES-GCM payload format.

### Pages and pairing

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/` | Returns the dashboard application HTML. The page handles login state in the browser. |
| `GET` | `/login` | Returns the login page. |
| `POST` | `/login` | Accepts `{"pin": "A3K9M2P4"}`; a valid, unexpired PIN returns `{"ok": true, "token": "<session_token>"}`. Invalid or expired PINs return an authentication error. |
| `GET` | `/auto-login?key=<pin>` | QR-code login flow using the same short-lived pending PIN. |

### API endpoints

The following endpoints use the indicated authentication mechanism.

| Method | Path | Authentication | Request | Success response / behavior |
|---|---|---|---|---|
| `POST` | `/api/command` | Bearer session token | JSON with `{"text": "…"}` or `{"enc": "<base64 AES-GCM payload>"}` | Queues the command and returns `{"ok": true}`. |
| `POST` | `/api/wake` | Bearer session token | No body required | Wakes the assistant and returns `{"ok": true}`. |
| `POST` | `/api/device-login` | Previously issued device token in JSON | `{"device_token": "…"}` | Reissues a session token and session key for a device token known to the running server. |
| `POST` | `/api/revoke-devices` | Bearer session token | No body required | Revokes remembered device sessions and returns counts. |
| `GET` | `/api/files` | Bearer session token | No body required | Returns the files currently in the dashboard upload directory. |
| `POST` | `/api/upload` | Bearer session token | `multipart/form-data` with a `file` field | Stores an upload (maximum 500 MB) and returns `{"ok": true, "name": "…", "size": 123}`. Requires `python-multipart`; otherwise the endpoint returns HTTP 503. |

### File download

- `GET /uploads/{filename}?token=<session_token>` downloads a file from the upload directory. This route uses the `token` query parameter because a browser download link cannot attach a custom Authorization header.

### WebSockets

- `WebSocket /ws?token=<session_token>` is the registered dashboard event socket. The `token` query parameter must contain a valid session token; otherwise the server closes the connection.
- `WebSocket /ws/phone-audio?token=<session_token>` is the authenticated phone-audio input socket.

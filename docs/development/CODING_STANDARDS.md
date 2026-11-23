# Coding Standards & Guidelines

This document outlines the software engineering and coding standards for contributing to **openagent**. It formalizes the practices established across `core/`, `gui/`, `actions/`, and `plugins/`.

---

## 1. General Python Standards

- **Python Target**: Python 3.11+. Code must be compatible with Python 3.11, 3.12, and 3.13.
- **Style Enforcement**: Follow PEP 8 guidelines, formatted with `ruff` (configured line length: 100 characters).
- **Type Annotations**:
  - Use modern typing syntax from Python 3.10+ (e.g. `list[str]`, `dict[str, Any]`, `X | None` instead of `typing.Union` / `typing.Optional`).
  - Add `from __future__ import annotations` at the top of every module.

---

## 2. Thread Safety & The Qt GUI Cardinal Rule

> **CRITICAL RULE**: Code in `actions/` and `plugins/` must NEVER create, modify, or interact with Qt widgets directly.

- Do **not** import or instantiate `QWidget`, `QMainWindow`, `QDialog`, `QApplication`, or `QThread` inside tools or plugins.
- Tools run on background worker threads. Creating Qt widgets from non-GUI threads triggers:
  ```
  QObject::setParent: Cannot set parent, new parent is in a different thread
  ```
- **Thread-Safe Communication Pattern**:
  - To interact with the user or display content, invoke thread-safe bridge methods on `player` (`player.write_log()`, `player.show_content()`, `player.show_quiz()`, `player.show_review()`, `player.show_video()`).
  - When extending GUI features in `gui/`, declare a `pyqtSignal` on `MainWindow`, connect it to a slot in `__init__`, and add a dispatcher method on `JarvisUI` in `ui.py` that emits the signal.

---

## 3. Plugin Architecture Rules (`plugins/`)

- Each plugin must reside in a single file: `plugins/<name>.py` (no leading underscore).
- Helper modules must use a leading underscore (e.g. `plugins/_helper.py`) so the plugin loader ignores them.
- Plugins must define:
  1. `PLUGIN = {"name": ..., "description": ..., "parameters": {"type": "OBJECT", ...}}`
  2. `def run(parameters: dict, player=None, session_memory=None) -> str`
- **Behavioral Constraints**:
  - **Never raise exceptions**: Wrap all external operations in `try/except` and return a spoken sentence explaining what occurred.
  - **Return quickly**: `run()` must return within a few seconds. Dispatch long-running tasks to `threading.Thread`.
  - **Safe `player` handling**: Always check `if player:` before calling UI methods (since test runners or phone dashboards pass `player=None`).

---

## 4. LLM & External Service Invocations

- Avoid ad-hoc, un-timeouted API requests.
- All Gemini calls outside of the live audio stream must route through `core/gemini.py`:
  ```python
  from core import gemini

  # Fast one-line classifications / extractions (returns empty string "" on failure)
  text = gemini.text(prompt, tier=gemini.FAST)

  # Structured JSON output (returns None on failure or unparseable output)
  data = gemini.as_json(prompt, tier=gemini.SMART)
  ```
- Handle model failures gracefully. Note the distinct fallback behaviors: `gemini.text()` defaults to an empty string `""` when no model answers, whereas `gemini.as_json()` returns `None` (or custom `default`). Checking only `if data is not None` on `text()` will not detect a failure.

---

## 5. Security & Defensive Coding

1. **Self-Termination Check**: Use `core.appidentity.own_pids()` before performing process management or window closures.
2. **Untrusted Data Fencing**: Always delineate third-party content with `<<<UNTRUSTED ...>>>` markers.
3. **Atomic File I/O**: Configuration updates must route through `memory.config_manager` using atomic `.tmp` replacement. Never open `config/api_keys.json` with write flags directly.
4. **No Hardcoded Secrets**: Never commit API keys, tokens, or personal identifiers.

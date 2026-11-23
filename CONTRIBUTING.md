# Contributing to openagent

Thank you for your interest in contributing to **openagent**! We welcome community contributions, bug reports, and pull requests.

---

## 1. Code of Conduct & Core Principles

- **Security First**: openagent operates with direct desktop automation capabilities. All changes must respect security boundaries, avoid executing untrusted remote code without confirmation, and never leak credentials.
- **Thread Safety**: Never touch Qt GUI widgets from background worker threads. See [Coding Standards](docs/development/CODING_STANDARDS.md).
- **Graceful Error Handling**: Tools and plugins must catch exceptions and return explanatory feedback rather than crashing the assistant.

---

## 2. Development Setup

1. Fork the repository on GitHub: `https://github.com/Sanj1th-M/openagent`.
2. Clone your fork locally:
   ```bash
   git clone https://github.com/<your-username>/openagent.git
   cd openagent
   ```
3. Set up a Python 3.11 or 3.12 virtual environment:
   ```bash
   python -m venv .venv
   source .venv/bin/activate       # macOS / Linux
   .\.venv\Scripts\Activate.ps1    # Windows
   ```
4. Install dependencies:
   ```bash
   python setup.py
   pip install pytest ruff mypy bandit pip-audit
   ```

---

## 3. Branching & Commit Conventions

### Branch Naming
- Features: `feat/<feature-name>` or `feature/<feature-name>`
- Bug Fixes: `fix/<bug-name>`
- Documentation: `docs/<topic>`

### Commit Messages
Follow [Conventional Commits](https://www.conventionalcommits.org/):
- `feat(actions): add new flight search integration`
- `fix(gui): resolve waveform clipping on high-DPI displays`
- `docs(api): document BaseLLM provider methods`
- `test(core): add unit tests for echo filter`

---

## 4. Pull Request Requirements

Before opening a PR:
1. Ensure the Pytest test suite passes:
   ```bash
   python -m pytest tests/ -v
   ```
2. Verify code formatting and linting:
   ```bash
   ruff check tests/ setup.py
   ruff format --check tests/ setup.py
   ```
3. Ensure no security warnings are introduced:
   ```bash
   bandit -ll -r actions core gui memory plugins main.py ui.py setup.py tests
   ```
4. Include documentation updates for new actions, plugins, or configuration options.

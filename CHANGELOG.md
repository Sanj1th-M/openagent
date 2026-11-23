# Changelog

All notable changes to **openagent** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [57.0.0] - 2026-11-23

### Added
- Comprehensive enterprise technical documentation suite:
  - System Architecture guide (`docs/ARCHITECTURE.md`) and Mermaid architecture diagrams (`docs/diagrams/`).
  - Installation manual (`docs/INSTALLATION.md`) and Configuration reference (`docs/CONFIGURATION.md`).
  - Core API and Provider interface documentation (`docs/API.md`).
  - Security architecture and threat model (`docs/SECURITY.md`).
  - Testing suite and CI/CD guide (`docs/TESTING.md`).
  - Packaging and deployment guide (`docs/DEPLOYMENT.md`).
  - Coding standards (`docs/development/CODING_STANDARDS.md`) and Release process (`docs/development/RELEASE_PROCESS.md`).
- Multi-provider architecture supporting Gemini Live, OpenAI-compatible local/remote models, local Whisper STT, and Piper/ElevenLabs/System TTS.
- Autonomous background task execution engine with round checkpoints, time budgets, and disk persistence.
- Remote mobile dashboard with TLS, PBKDF2 key derivation, and AES-256-GCM encrypted command streams.
- Software-rendered 3D holographic wireframe avatar running on PyQt6 and numpy without GPU dependencies.
- Local wake-word detection engine and anti-self-echo circular buffer filtering.

### Changed
- Standardized product branding as **openagent** across package manifests, installer, and documentation.
- Maintained customizable assistant display name (default: "Jarvis") in UI and configuration settings.
- Enforced thread-safe communication bridges (`player` / `JarvisUI`) between worker threads and the Qt main loop.

### Security
- Implemented action confirmation gate (`core/confirm.py`) for destructive system operations.
- Added self-termination shield (`core/appidentity.py`) preventing the assistant from closing its own host process or parent shell.
- Established strict prompt fencing (`<<<UNTRUSTED ...>>>`) for external web data to mitigate indirect prompt injection.
- Automated CI security workflows running Bandit AST scanning, `pip-audit` CVE checks, and GitHub CodeQL static analysis.

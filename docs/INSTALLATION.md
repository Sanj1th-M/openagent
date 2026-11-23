# Installation Guide

This guide provides instructions for installing and running **openagent** across supported operating systems.

---

## 1. System Requirements

### Hardware Requirements
- **Processor**: Intel Core i5 / AMD Ryzen 5 or Apple Silicon (M1/M2/M3) equivalent or higher.
- **RAM**: 8 GB minimum (16 GB recommended for local Whisper STT or Piper TTS models).
- **Disk Space**: ~2 GB free disk space (additional space required if downloading local AI model weights).
- **Audio**: Microphone input and speaker/headphone output.
- **Display**: Minimum 1280x720 screen resolution (optimized for high-DPI displays).

### Supported Operating Systems
- **Windows**: Windows 10 (64-bit) or Windows 11.
- **macOS**: macOS 12 (Monterey) or later (Apple Silicon and Intel).
- **Linux**: Ubuntu 22.04 LTS+, Debian 12+, Fedora 38+, or Arch Linux.

### Python Requirements
- **Python Version**: **Python 3.11** or **Python 3.12** (Python 3.13 is supported in warning mode; Python < 3.11 is strictly unsupported).

---

## 2. Prerequisites

### Git Installation
- **Windows**: Download and install [Git for Windows](https://git-scm.com/download/win).
- **macOS**: Install via Homebrew:
  ```bash
  brew install git
  ```
- **Debian / Ubuntu**:
  ```bash
  sudo apt-get update && sudo apt-get install -y git
  ```
- **Fedora**:
  ```bash
  sudo dnf install -y git
  ```
- **Arch Linux**:
  ```bash
  sudo pacman -S --noconfirm git
  ```

### System Audio & GUI Libraries (Linux Only)
Linux distributions require core multimedia and X11/Wayland libraries for PyQt6 and PortAudio:

**Debian / Ubuntu**:
```bash
sudo apt-get update
sudo apt-get install -y \
    libegl1 libgl1 libfontconfig1 libxkbcommon-x11-0 \
    libdbus-1-3 libasound2-dev libportaudio2 xvfb
```

**Fedora**:
```bash
sudo dnf install -y \
    mesa-libEGL mesa-libGL fontconfig libxkbcommon-x11 \
    dbus-libs alsa-lib-devel portaudio-devel xorg-x11-server-Xvfb
```

**Arch Linux**:
```bash
sudo pacman -S --noconfirm \
    mesa fontconfig libxkbcommon-x11 \
    dbus alsa-lib portaudio xorg-server-xvfb
```

---

## 3. Installation Steps

### Step 1: Clone Repository
```bash
git clone https://github.com/Sanj1th-M/openagent.git
cd openagent
```

### Step 2: Set Up Virtual Environment
Create and activate an isolated virtual environment:

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**Windows (Command Prompt):**
```cmd
python -m venv .venv
.\.venv\Scripts\activate.bat
```

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Step 3: Run the Setup Installer
openagent includes an automated, cross-platform installer (`setup.py`) that filters dependencies matching your operating system and installs required Playwright browsers:

```bash
python setup.py
```

*Note: Alternatively, you can manually install requirements using pip:*
```bash
pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium firefox
```

---

## 4. First-Time Startup & Configuration

Run the application:
```bash
python main.py
```

Upon initial launch:
1. The **Setup Wizard** or **API Key Prompt** will appear.
2. Enter your **Google Gemini API Key** (obtainable from [Google AI Studio](https://aistudio.google.com/)).
3. The configuration will be stored locally and securely in `config/api_keys.json`.
4. Test the microphone input and speakers via the on-screen heads-up display (HUD).

---

## 5. Optional Provider Installation

### Local Wake-Word Engine ("Hey Jarvis")
Local wake-word detection is opt-in and does not run by default:
- Click the **Settings Gear (⚙)** in the GUI → Navigate to **WAKE WORD**.
- Enable wake-word detection or install required weights with one click.

### Alternative AI Providers
If using local or alternative providers (OpenAI, Local Whisper, Piper TTS):
1. Navigate to the **Providers Panel** (Sidebar drawer).
2. Configure your endpoints (e.g., local Ollama, vLLM, or LM Studio endpoints at `http://localhost:11434/v1`).
3. For local Whisper or Piper TTS, ensure dependencies are present in your environment.

---

## 6. Audio Device Configuration

If the assistant fails to capture audio or you have multiple audio interfaces:
1. Run `python -m core.audio_devices` to list all detected input and output interfaces.
2. Select your default input/output devices in the settings panel inside the application GUI.
3. Ensure microphone permissions are granted in your operating system privacy settings.

---

## 7. Troubleshooting & Common Issues

| Issue | Cause | Solution |
|---|---|---|
| `❌ Python X.X detected — openagent needs at least Python 3.11` | Unsupported Python version | Install Python 3.11 or 3.12 and create a fresh virtual environment. |
| `QObject::setParent: Cannot set parent, new parent is in a different thread` | Direct widget access from worker | Do not modify GUI code outside the Qt main thread. Follow `AGENTS.md`. |
| `ModuleNotFoundError: No module named 'psutil'` | Missing dependencies | Activate `.venv` and run `python setup.py` or `pip install -r requirements.txt`. |
| `core/face_model.obj is missing or truncated` | Incomplete Git clone | Re-fetch the file: `git checkout core/face_model.obj`. |
| `Playwright browsers were not installed` | Network or firewall block during setup | Run `python -m playwright install chromium firefox` manually. |
| `PortAudio / sounddevice error` | Missing host audio drivers | On Linux: `sudo apt install libportaudio2 libasound2-dev`. On Windows: verify audio devices in Device Manager. |

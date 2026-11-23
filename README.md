# WT-XVM — War Thunder Tactical Overlay

**WT-XVM** is an open-source tactical overlay and second-screen dashboard for **War Thunder** on Windows. It displays player statistics, real-time battle telemetry, enemy aircraft tracking, FPE timers, and tactical **"Воздух!" / "Air!"** callouts in a transparent click-through overlay (plus any browser on a second monitor).

> **📥 Скачать готовый EXE / Direct Download:**  
> **[⬇ Скачать `WT-XVM.exe` (прямая ссылка)](https://github.com/Ikaros0102/XVM_WarThunder/raw/main/dist/WT-XVM.exe)** *(или открыть [`dist/WT-XVM.exe`](dist/WT-XVM.exe) в репозитории / [Releases](https://github.com/Ikaros0102/XVM_WarThunder/releases))*

> **Open Source (MIT License):** Anyone is free to use, copy, modify, improve, and redistribute this project. See [LICENSE](LICENSE).

---

## Anti-Cheat Safety Design

WT-XVM strictly operates as an external companion tool using only permitted data sources:

- **NO memory reading** (`aces.exe` is never opened or inspected).
- **NO DLL injections**, DirectX/Vulkan hooks, or graphics API overlays.
- **NO packet sniffing** or game input automation/macros.
- **ONLY** reads War Thunder's official local telemetry web server (`http://localhost:8111`), captures the scoreboard strictly on user `Tab` keypresses for local OCR, fetches public ThunderSkill profile pages with polite throttling and SQLite caching, and draws an independent transparent Edge WebView2 window (`WS_EX_TRANSPARENT`) on top of the game.

> **Game Window Requirement:** Use **Borderless Windowed** (**Полноэкранный в окне**) mode in War Thunder so a standard top-most Windows overlay can render above the game.

---

## Features

- **Allies & Enemies Panels ("Уши")**:
  - Team assignment strictly locked to `Tab` scoreboard OCR (`RapidOCR` with bundled Cyrillic + Latin recognition model), merged non-destructively across rescans (with 30s debounce) and matched against live UTF-8 logs via fuzzy homoglyph matching.
  - Shows **Nicknames** (clan tags stripped automatically), **Current Vehicle**, **WR% / K/D / Battles** (AB/RB/SB mode selector), **Match K/D**, **6s FPE Cooldown Timer**, **Nuke Threat (`☢`)**, **Disconnect Status**, and quick **1-click ThunderSkill/Gaijin lookup link**.
  - Every column can be toggled individually from the `F9` bar or **Настройки**.
- **Tactical Air Alerts («Воздух!»)**:
  - Parses team radio alerts with azimuth and altitude (e.g. `Воздух! 330°, высота 1000 м` / `Air! 330°, altitude 1000 m`) and ignores short altitude-less "Воздух!" pings.
  - Switchable between **Rectangular Banner** (`rect`) and **Compact Circular Radar Badge** (`circle`).
- **Enemy Air Counter**:
  - Counts active enemy planes and helicopters separately (`Самолёты` / `Вертолёты`) and ignores Recon Micro UAVs (`Recon Micro` / `[ии] ...`).
- **Full Per-Widget Customization**:
  - Drag-and-drop position, independent **scale** (`0.5x`–`1.8x`), and independent **opacity** (`10%`–`100%`) per widget (`ally_panel`, `enemy_panel`, `air_alert`, `enemy_air_counter`).
- **Convenient Hotkeys & Second Screen UI**:
  - **`Tab`** (hold briefly in battle): Scan teams and queue stats lookups.
  - **`F9`**: Toggle interactive layout edit mode (drag widgets, toggle columns, adjust scale/opacity, switch Air widget style, open Settings, or Exit app cleanly).
  - **Configurable Show/Hide Hotkey** (default **`F8`**, customizable in **Настройки**): Instantly hides or shows the entire overlay when you want maximum screen clarity.
  - **Second Screen Dashboard**: Open `http://127.0.0.1:8123` in any browser on a second monitor.
- **Debug Diagnostics Mode**:
  - Optional recording of raw `/map_info.json`, `/hudmsg`, `/gamechat`, `Tab` OCR crops/boxes, and stats lookups into `logs/debug_sessions/`.

---

## Quick Start (End Users) / Быстрый старт

1. **[⬇ Скачайте `WT-XVM.exe`](https://github.com/Ikaros0102/XVM_WarThunder/raw/main/dist/WT-XVM.exe)** (или соберите `dist\WT-XVM.exe` самостоятельно).
2. Switch War Thunder graphics settings to **Borderless Windowed** (**Полноэкранный в окне**).
3. Run `WT-XVM.exe` (a tray icon and transparent overlay start automatically).
4. Once inside a match, hold `Tab` briefly (~0.3s) to scan both team rosters.
5. Press `F9` anytime to drag panels, adjust column visibility/opacity/scale, open **Настройки**, or click **Выход** to exit cleanly.
6. Press **`F8`** (or your custom hotkey in Settings) anytime to hide/show the overlay.

---

## Development Setup & Building from Source

### Prerequisites

- Windows 10/11 (x64) with Microsoft Edge WebView2 Runtime
- Python 3.11+
- Node.js 18+ & npm

### Install Dependencies

```powershell
# Backend virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e ".[dev]"

# Frontend dependencies
npm --prefix frontend install
```

### Run from Source

```powershell
# Normal desktop overlay + tray + Second Screen UI (http://127.0.0.1:8123)
.\.venv\Scripts\python.exe -m wt_xvm.main

# Headless Second Screen UI with built-in mock battle replay
.\.venv\Scripts\python.exe -m wt_xvm.main --headless --mock --mock-lang ru
```

### Run Tests

```powershell
.\.venv\Scripts\pytest.exe -v
```

### Build Single `.exe`

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

This compiles the React/Vite SPA (`frontend\dist`) and packages `dist\WT-XVM.exe` via PyInstaller (`packaging\wt_xvm.spec`).

---

## Project Architecture

```text
wt_xvm/
  main.py                  # Entrypoint (WebView2 overlay, FastAPI server, system tray)
  config.py                # Pydantic config schema & atomic config.json persistence
  core/
    poller.py              # Async localhost:8111 poller + hangar cursor drain + boundary guards
    battle_state.py        # Thread-safe match state, K/D, FPE, nuke, air threats
    log_parser.py          # RU/EN HUD log & chat parser
    nick_cleaner.py        # Clan-tag & console-tag parser, homoglyph normalization
    debug_recorder.py      # Optional JSONL + PNG session recorder
  ocr/
    tab_watcher.py         # Non-hooking Tab key watcher (30s debounce)
    scoreboard_ocr.py      # RapidOCR scoreboard column parser
  stats/
    providers.py           # Polite throttled ThunderSkill HTML parser + multi-candidate fallback
    cache.py               # 7-day TTL SQLite stats cache
    service.py             # Async worker queue & circuit breaker
  desktop/
    overlay_window.py      # Win32 WS_EX_TRANSPARENT / DWM alpha WebView2 window & hotkeys
    tray.py                # System tray menu
  api/
    server.py              # FastAPI REST endpoints & SPA serving
    ws_hub.py              # Live WebSocket state/config push
frontend/                  # React + TypeScript + Vite SPA
tests/                     # Pytest test suite
```

---

## Contributing & License

Pull requests, bug fixes, OCR improvements, and additional stats providers are welcome! Fork the repo, make your changes, verify with `pytest -v`, and submit a PR.

Licensed under the **MIT License** — see [LICENSE](LICENSE).

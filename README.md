# Work Timer

A small always-on-top desktop gadget for Windows that tracks active working hours and idle time. Lives as a draggable lavender pill in the corner of your screen and a cat icon in the system tray. Closes and reopens right where you left off.

![pill](docs/pill.png) ![panel](docs/panel.png)

## Features

- **Pill mode**: frameless, always-on-top, draggable. Shows current work time. Text changes color when you're idle.
- **Click to expand** into a full panel with separate Work and Idle counters, plus Start, Pause, Reset, and View Log controls.
- **System tray icon** with right-click menu (Show/Hide pill, Start, Pause, View Log, Reset, Quit). Double-click toggles the pill. Hover tooltip shows live time.
- **Idle detection** via Win32 `GetLastInputInfo`. After 3 minutes of no keyboard or mouse input, an idle counter runs in parallel.
- **Persistent state**: closing and reopening resumes the same totals in the same screen position.
- **Per-day log**: `daily_log.json` accumulates work and idle seconds per date, viewable in-app.
- **Portable user data**: state is stored in `%APPDATA%\WorkTimer\`, so the launcher can live anywhere.

## Installation

Requires Python 3.8+ on Windows. Tkinter must be included (it is in the standard installer from python.org if "tcl/tk and IDLE" is checked during install).

```
pip install pystray Pillow
```

Then double-click `WorkTimer.bat` to launch. To launch it like an installed app:

1. Right-click `WorkTimer.bat` → Send to → Desktop (create shortcut).
2. Right-click the shortcut → Properties → Change Icon → browse to `icon.ico`.
3. Optional: drag a copy of the shortcut into `shell:startup` (run from Win+R) for auto-launch on login.

### Why a `.bat` launcher and not an `.exe`?

A PyInstaller-built executable is blocked on Windows 11 by Smart App Control with no override option (Smart App Control, unlike SmartScreen, has no per-application allow). The `.bat` invokes `pythonw work_timer.py`, which Smart App Control allows because the Python interpreter itself is signed. From the user's perspective the experience is identical: double-click to launch, custom icon on the shortcut, no console window.

## Controls

| Action | Result |
|---|---|
| Click pill | Toggle expanded panel |
| Drag pill | Move anywhere on screen |
| Right-click pill | Quick menu |
| Right-click tray icon | Full menu |
| Double-click tray icon | Show/Hide pill |
| Reset | Clear current session totals (daily log preserved) |

## Files

| File | Purpose |
|---|---|
| `work_timer.py` | Main application |
| `icon.ico` | Multi-resolution icon (16-256 px) |
| `WorkTimer.bat` | Launcher invoking `pythonw` |
| `WorkTimer_Technical_Report.pdf` | Detailed technical report covering design and issues encountered |

User data lives at `%APPDATA%\WorkTimer\` (`timer_state.json` and `daily_log.json`), created automatically on first run.

## Architecture

- **Tkinter** for the UI (frameless `overrideredirect` window with color-keyed transparent background for the rounded pill shape).
- **Custom Canvas widget** renders the gradient pill in pure pixel-by-pixel drawing.
- **`pystray`** for the system tray icon, running on a daemon thread; menu actions marshal back to the Tk main thread via `root.after`.
- **Win32 `GetLastInputInfo`** via `ctypes` for idle detection (no extra packages).
- **Monotonic-clock-anchored** timing model resilient to system clock changes.

See `WorkTimer_Technical_Report.pdf` for full details on system design, issues encountered during development, and resolutions.

## Known Limitations

- Windows-only. Idle detection uses Win32 APIs.
- System-wide idle detection: long video or reading sessions without input are counted as idle.
- A timer tick that crosses midnight is attributed to the new day (max 1 second skew).
- Reset preserves the daily log. To clear history, delete `daily_log.json` from `%APPDATA%\WorkTimer\`.

## License

MIT

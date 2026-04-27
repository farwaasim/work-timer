"""
Work Timer — a small always-on-top desktop gadget for Windows
with a system tray icon.

Pill (collapsed): lavender-cream gradient capsule. Drag to move,
click to expand, right-click for quick menu.

Panel (expanded): cream card with Work/Idle counters and controls.

Tray icon (system tray, near the clock):
  - Double-click: toggle pill visibility
  - Right-click: full menu (Show/Hide, Start/Pause, View Log, Reset, Quit)

State + last screen position persist to %APPDATA%\\WorkTimer\\ so the
.exe can live anywhere and your data follows your user account.

Idle is detected via Win32 GetLastInputInfo (stdlib only).

Requires: pystray, Pillow  (pip install pystray pillow)
"""

import ctypes
import json
import os
import shutil
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from datetime import date
from tkinter import messagebox, ttk

from PIL import Image
import pystray

# --- Paths ---------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))

APPDATA = os.environ.get("APPDATA") or os.path.expanduser("~")
DATA_DIR = os.path.join(APPDATA, "WorkTimer")
os.makedirs(DATA_DIR, exist_ok=True)

STATE_FILE = os.path.join(DATA_DIR, "timer_state.json")
LOG_FILE = os.path.join(DATA_DIR, "daily_log.json")


def _migrate_legacy_state():
    legacy_state = os.path.join(BASE_DIR, "timer_state.json")
    legacy_log = os.path.join(BASE_DIR, "daily_log.json")
    if os.path.exists(legacy_state) and not os.path.exists(STATE_FILE):
        try:
            shutil.move(legacy_state, STATE_FILE)
        except OSError:
            pass
    if os.path.exists(legacy_log) and not os.path.exists(LOG_FILE):
        try:
            shutil.move(legacy_log, LOG_FILE)
        except OSError:
            pass


_migrate_legacy_state()


def _resource_path(name):
    """Locate a bundled resource (icon.ico) whether running from source
    or from a PyInstaller --onefile bundle."""
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(os.path.join(sys._MEIPASS, name))
    candidates.append(os.path.join(BASE_DIR, name))
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


# --- Config --------------------------------------------------------------
IDLE_THRESHOLD_SECONDS = 180
TICK_MS = 1000

PILL_W = 150
PILL_H = 44

TRANSPARENT_KEY = "magenta"

GRADIENT_STOPS = [
    (0.00, (199, 168, 245)),
    (0.50, (230, 217, 255)),
    (1.00, (253, 246, 230)),
]

PANEL_BG = "#1c1626"
PANEL_CARD = "#fffaf0"
PANEL_FG = "#3d2766"
PANEL_MUTED = "#8a7aa3"
PANEL_ACCENT = "#7c5fc7"
PANEL_IDLE = "#c789c7"

PILL_TEXT = "#3d2766"
PILL_TEXT_IDLE = "#9b3d8c"


# --- Win32 idle detection ------------------------------------------------
class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def get_idle_seconds():
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0.0
    millis_since_boot = ctypes.windll.kernel32.GetTickCount()
    return (millis_since_boot - lii.dwTime) / 1000.0


# --- Persistence ---------------------------------------------------------
def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "work_elapsed": float(data.get("work_elapsed", 0)),
            "idle_elapsed": float(data.get("idle_elapsed", 0)),
            "pos_x": data.get("pos_x"),
            "pos_y": data.get("pos_y"),
        }
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return {"work_elapsed": 0.0, "idle_elapsed": 0.0,
                "pos_x": None, "pos_y": None}


def save_state(work_elapsed, idle_elapsed, pos_x=None, pos_y=None):
    payload = {"work_elapsed": work_elapsed, "idle_elapsed": idle_elapsed}
    if pos_x is not None and pos_y is not None:
        payload["pos_x"] = int(pos_x)
        payload["pos_y"] = int(pos_y)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def load_log():
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_log(log):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, sort_keys=True)


# --- Helpers -------------------------------------------------------------
def format_hms(total_seconds):
    total_seconds = int(total_seconds)
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def today_key():
    return date.today().isoformat()


def lerp(a, b, t):
    return a + (b - a) * t


def gradient_color(t):
    t = max(0.0, min(1.0, t))
    for i in range(len(GRADIENT_STOPS) - 1):
        t0, c0 = GRADIENT_STOPS[i]
        t1, c1 = GRADIENT_STOPS[i + 1]
        if t0 <= t <= t1:
            local = 0 if t1 == t0 else (t - t0) / (t1 - t0)
            r = int(lerp(c0[0], c1[0], local))
            g = int(lerp(c0[1], c1[1], local))
            b = int(lerp(c0[2], c1[2], local))
            return f"#{r:02x}{g:02x}{b:02x}"
    return "#ffffff"


# --- Pill canvas ---------------------------------------------------------
class PillCanvas(tk.Canvas):
    def __init__(self, master, text_var, **kw):
        super().__init__(master, width=PILL_W, height=PILL_H,
                         bg=TRANSPARENT_KEY, highlightthickness=0, bd=0, **kw)
        self.text_var = text_var
        self._text_color = PILL_TEXT
        self._draw()

    def set_text_color(self, color):
        if color != self._text_color:
            self._text_color = color
            self.itemconfigure("pill_text", fill=color)

    def _draw(self):
        self.delete("all")
        radius = PILL_H // 2
        for x in range(PILL_W):
            t = x / max(1, PILL_W - 1)
            color = gradient_color(t)
            for y in range(PILL_H):
                if x < radius:
                    dx = radius - x; dy = y - radius
                    if dx * dx + dy * dy > radius * radius:
                        continue
                elif x > PILL_W - radius - 1:
                    dx = x - (PILL_W - radius - 1); dy = y - radius
                    if dx * dx + dy * dy > radius * radius:
                        continue
                self.create_line(x, y, x + 1, y, fill=color)
        self.create_arc(2, 2, PILL_W - 2, PILL_H, start=20, extent=140,
                        style="arc", outline="#ffffff", width=1)
        self.create_text(PILL_W // 2, PILL_H // 2, text=self.text_var.get(),
                         fill=self._text_color, font=("Segoe UI Semibold", 14),
                         tags=("pill_text",))

    def update_text(self):
        self.itemconfigure("pill_text", text=self.text_var.get())


# --- App -----------------------------------------------------------------
class WorkTimerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Work Timer")

        icon_path = _resource_path("icon.ico")
        if icon_path:
            try:
                self.root.iconbitmap(default=icon_path)
            except tk.TclError:
                pass

        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", TRANSPARENT_KEY)
        self.root.configure(bg=TRANSPARENT_KEY)

        state = load_state()
        self.saved_work = state["work_elapsed"]
        self.saved_idle = state["idle_elapsed"]
        self.last_pos = (state["pos_x"], state["pos_y"])

        self.work_run_started_at = None
        self.last_tick_monotonic = None
        self.is_idle_now = False

        self._drag_offset = (0, 0)
        self._drag_moved = False
        self._press_pos = (0, 0)

        self.work_var = tk.StringVar(value=format_hms(self.saved_work))
        self.idle_var = tk.StringVar(value=format_hms(self.saved_idle))
        self.status_var = tk.StringVar(value="Paused")

        self.pill_canvas = PillCanvas(root, self.work_var)
        self.panel_frame = self._build_panel(root)

        self._bind_drag(self.pill_canvas)

        self.menu = tk.Menu(root, tearoff=0)
        self.menu.add_command(label="Start", command=self.start)
        self.menu.add_command(label="Pause", command=self.pause)
        self.menu.add_separator()
        self.menu.add_command(label="View Log", command=self.show_log)
        self.menu.add_command(label="Reset", command=self.reset)
        self.menu.add_separator()
        self.menu.add_command(label="Hide pill", command=self.hide_window)
        self.menu.add_command(label="Quit", command=self.quit_app)
        self.pill_canvas.bind("<Button-3>", self._show_context_menu)

        self.is_expanded = False
        self.pill_visible = True
        self.pill_canvas.pack()
        self._restore_position()

        # Tk's built-in close button is gone (overrideredirect). The tray
        # icon's Quit is the sanctioned way out. We still bind WM_DELETE
        # for safety in case the WM sends one.
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)

        # --- Tray icon ---
        self.tray_icon = self._build_tray_icon()
        # Run pystray in a daemon thread so the Tk mainloop owns the
        # main thread.
        self._tray_thread = threading.Thread(
            target=self.tray_icon.run, daemon=True
        )
        self._tray_thread.start()

        self._tick()

    # --- Tray ---
    def _build_tray_icon(self):
        icon_path = _resource_path("icon.ico")
        if icon_path:
            image = Image.open(icon_path)
        else:
            # Fallback: a plain lavender square so the app still runs.
            image = Image.new("RGB", (64, 64), (124, 95, 199))

        # pystray menu callbacks run on the tray thread. Marshal Tk work
        # back onto the Tk main thread via root.after(0, ...).
        def on_root(fn):
            return lambda *_: self.root.after(0, fn)

        menu = pystray.Menu(
            pystray.MenuItem(
                "Show pill",
                on_root(self.show_window),
                default=True,  # double-click does this
                visible=lambda item: not self.pill_visible,
            ),
            pystray.MenuItem(
                "Hide pill",
                on_root(self.hide_window),
                default=True,
                visible=lambda item: self.pill_visible,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start", on_root(self.start),
                             enabled=lambda i: self.work_run_started_at is None),
            pystray.MenuItem("Pause", on_root(self.pause),
                             enabled=lambda i: self.work_run_started_at is not None),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("View Log", on_root(self.show_log)),
            pystray.MenuItem("Reset", on_root(self.reset)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", on_root(self.quit_app)),
        )
        return pystray.Icon("WorkTimer", image, "Work Timer", menu)

    def _tray_set_title(self, text):
        # Tooltip shown when hovering the tray icon.
        try:
            self.tray_icon.title = text
        except Exception:
            pass

    def show_window(self):
        if not self.pill_visible:
            self.root.deiconify()
            self.pill_visible = True

    def hide_window(self):
        if self.pill_visible:
            self.root.withdraw()
            self.pill_visible = False

    # --- Panel UI ---
    def _build_panel(self, root):
        outer = tk.Frame(root, bg=TRANSPARENT_KEY)
        card = tk.Frame(outer, bg=PANEL_CARD, padx=18, pady=14,
                        highlightthickness=2, highlightbackground=PANEL_ACCENT)
        card.pack(padx=4, pady=4)

        tk.Label(card, text="WORK", bg=PANEL_CARD, fg=PANEL_MUTED,
                 font=("Segoe UI", 8, "bold")).pack()
        tk.Label(card, textvariable=self.work_var, bg=PANEL_CARD, fg=PANEL_FG,
                 font=("Segoe UI Semibold", 26)).pack()

        tk.Label(card, text="IDLE", bg=PANEL_CARD, fg=PANEL_MUTED,
                 font=("Segoe UI", 8, "bold")).pack(pady=(8, 0))
        self.idle_label = tk.Label(card, textvariable=self.idle_var,
                                   bg=PANEL_CARD, fg=PANEL_MUTED,
                                   font=("Segoe UI", 14))
        self.idle_label.pack()

        btn_row = tk.Frame(card, bg=PANEL_CARD)
        btn_row.pack(pady=(12, 4))
        self.start_btn = self._mk_btn(btn_row, "Start", self.start, accent=True)
        self.start_btn.grid(row=0, column=0, padx=3)
        self.pause_btn = self._mk_btn(btn_row, "Pause", self.pause)
        self.pause_btn.grid(row=0, column=1, padx=3)
        self.pause_btn.config(state=tk.DISABLED)
        self.reset_btn = self._mk_btn(btn_row, "Reset", self.reset)
        self.reset_btn.grid(row=0, column=2, padx=3)

        btn_row2 = tk.Frame(card, bg=PANEL_CARD)
        btn_row2.pack()
        self._mk_btn(btn_row2, "View Log", self.show_log).grid(row=0, column=0, padx=3)
        self._mk_btn(btn_row2, "Collapse", self.collapse).grid(row=0, column=1, padx=3)
        self._mk_btn(btn_row2, "Quit", self.quit_app).grid(row=0, column=2, padx=3)

        tk.Label(card, textvariable=self.status_var, bg=PANEL_CARD,
                 fg=PANEL_MUTED, font=("Segoe UI", 9)).pack(pady=(8, 0))

        self._bind_drag(card)
        for child in card.winfo_children():
            if isinstance(child, tk.Label):
                self._bind_drag(child)
        card.bind("<Button-3>", self._show_context_menu)
        return outer

    def _mk_btn(self, parent, text, cmd, accent=False):
        bg = PANEL_ACCENT if accent else "#e6d9ff"
        fg = "#ffffff" if accent else PANEL_FG
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                         activebackground=bg, activeforeground=fg,
                         relief="flat", bd=0, padx=10, pady=4,
                         font=("Segoe UI", 9, "bold"), cursor="hand2")

    # --- Drag binding ---
    def _bind_drag(self, widget):
        widget.bind("<ButtonPress-1>", self._drag_start)
        widget.bind("<B1-Motion>", self._drag_motion)
        widget.bind("<ButtonRelease-1>", self._drag_end)

    # --- Window position ---
    def _restore_position(self):
        x, y = self.last_pos
        if x is None or y is None:
            self.root.update_idletasks()
            sw = self.root.winfo_screenwidth()
            x = sw - PILL_W - 30
            y = 60
        self.root.geometry(f"+{int(x)}+{int(y)}")

    def _current_position(self):
        return self.root.winfo_x(), self.root.winfo_y()

    # --- Expand / collapse ---
    def expand(self):
        if self.is_expanded:
            return
        self.pill_canvas.pack_forget()
        self.panel_frame.pack()
        self.is_expanded = True

    def collapse(self):
        if not self.is_expanded:
            return
        self.panel_frame.pack_forget()
        self.pill_canvas.pack()
        self.is_expanded = False

    def toggle(self):
        if self.is_expanded:
            self.collapse()
        else:
            self.expand()

    # --- Drag (and click-to-toggle if not dragged) ---
    def _drag_start(self, event):
        self._drag_offset = (event.x_root - self.root.winfo_x(),
                             event.y_root - self.root.winfo_y())
        self._press_pos = (event.x_root, event.y_root)
        self._drag_moved = False

    def _drag_motion(self, event):
        dx = event.x_root - self._press_pos[0]
        dy = event.y_root - self._press_pos[1]
        if not self._drag_moved and (dx * dx + dy * dy) < 9:
            return
        self._drag_moved = True
        new_x = event.x_root - self._drag_offset[0]
        new_y = event.y_root - self._drag_offset[1]
        self.root.geometry(f"+{new_x}+{new_y}")

    def _drag_end(self, event):
        if not self._drag_moved:
            self.toggle()
        else:
            x, y = self._current_position()
            save_state(self.current_work_total(), self.saved_idle, x, y)

    def _show_context_menu(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    # --- Computed totals ---
    def current_work_total(self):
        if self.work_run_started_at is None:
            return self.saved_work
        return self.saved_work + (time.monotonic() - self.work_run_started_at)

    # --- Buttons ---
    def start(self):
        if self.work_run_started_at is not None:
            return
        now = time.monotonic()
        self.work_run_started_at = now
        self.last_tick_monotonic = now
        self.start_btn.config(state=tk.DISABLED)
        self.pause_btn.config(state=tk.NORMAL)
        self.status_var.set("Running")

    def pause(self):
        if self.work_run_started_at is None:
            return
        self.saved_work = self.current_work_total()
        self.work_run_started_at = None
        self.last_tick_monotonic = None
        x, y = self._current_position()
        save_state(self.saved_work, self.saved_idle, x, y)
        self.start_btn.config(state=tk.NORMAL)
        self.pause_btn.config(state=tk.DISABLED)
        self.status_var.set("Paused")

    def reset(self):
        if not messagebox.askyesno(
            "Reset",
            "Clear current work and idle totals?\n(The daily log is kept.)",
        ):
            return
        self.work_run_started_at = None
        self.last_tick_monotonic = None
        self.saved_work = 0.0
        self.saved_idle = 0.0
        x, y = self._current_position()
        save_state(0.0, 0.0, x, y)
        self.start_btn.config(state=tk.NORMAL)
        self.pause_btn.config(state=tk.DISABLED)
        self.status_var.set("Paused")

    # --- Per-day log ---
    def _add_to_log(self, work_delta, idle_delta):
        if work_delta <= 0 and idle_delta <= 0:
            return
        log = load_log()
        today = today_key()
        entry = log.get(today, {"work_seconds": 0.0, "idle_seconds": 0.0})
        entry["work_seconds"] = float(entry.get("work_seconds", 0)) + max(0.0, work_delta)
        entry["idle_seconds"] = float(entry.get("idle_seconds", 0)) + max(0.0, idle_delta)
        log[today] = entry
        save_log(log)

    # --- Tick loop ---
    def _tick(self):
        try:
            if self.work_run_started_at is not None:
                now = time.monotonic()
                tick_delta = now - (self.last_tick_monotonic or now)
                self.last_tick_monotonic = now

                work_delta = tick_delta
                idle_secs = get_idle_seconds()
                self.is_idle_now = idle_secs >= IDLE_THRESHOLD_SECONDS
                idle_delta = tick_delta if self.is_idle_now else 0.0

                self.saved_idle += idle_delta
                self._add_to_log(work_delta, idle_delta)
                x, y = self._current_position()
                save_state(self.current_work_total(), self.saved_idle, x, y)

                self.status_var.set(
                    "Running (idle)" if self.is_idle_now else "Running"
                )

            self.pill_canvas.set_text_color(
                PILL_TEXT_IDLE if self.is_idle_now else PILL_TEXT
            )
            self.idle_label.config(
                fg=PANEL_IDLE if self.is_idle_now else PANEL_MUTED
            )

            cur_work = format_hms(self.current_work_total())
            self.work_var.set(cur_work)
            self.idle_var.set(format_hms(self.saved_idle))
            self.pill_canvas.update_text()

            # Update tray tooltip with the live time.
            running = self.work_run_started_at is not None
            self._tray_set_title(
                f"Work Timer — {cur_work} ({'running' if running else 'paused'})"
            )
        finally:
            self.root.after(TICK_MS, self._tick)

    # --- Log viewer ---
    def show_log(self):
        # Make sure the gadget is on screen so the dialog has a parent
        # the user can locate.
        self.show_window()

        log = load_log()
        win = tk.Toplevel(self.root)
        win.title("Daily Log")
        win.geometry("400x360")
        win.configure(bg=PANEL_CARD)
        win.attributes("-topmost", True)

        tk.Label(win, text="Daily Log", bg=PANEL_CARD, fg=PANEL_FG,
                 font=("Segoe UI Semibold", 14)).pack(pady=(12, 6))

        style = ttk.Style(win)
        style.theme_use("clam")
        style.configure("Log.Treeview", background="#fffaf0",
                        fieldbackground="#fffaf0", foreground=PANEL_FG,
                        rowheight=24, borderwidth=0)
        style.configure("Log.Treeview.Heading", background=PANEL_ACCENT,
                        foreground="#ffffff", font=("Segoe UI", 9, "bold"),
                        borderwidth=0)

        cols = ("date", "work", "idle")
        tree = ttk.Treeview(win, columns=cols, show="headings", height=12,
                            style="Log.Treeview")
        tree.heading("date", text="Date"); tree.heading("work", text="Work"); tree.heading("idle", text="Idle")
        tree.column("date", width=130, anchor="center")
        tree.column("work", width=110, anchor="center")
        tree.column("idle", width=110, anchor="center")
        tree.pack(fill="both", expand=True, padx=14, pady=8)

        if not log:
            tree.insert("", "end", values=("(no entries yet)", "", ""))
        else:
            for day in sorted(log.keys(), reverse=True):
                entry = log[day]
                tree.insert("", "end", values=(
                    day,
                    format_hms(entry.get("work_seconds", 0)),
                    format_hms(entry.get("idle_seconds", 0)),
                ))

        tk.Button(win, text="Close", command=win.destroy,
                  bg=PANEL_ACCENT, fg="#ffffff", activebackground=PANEL_ACCENT,
                  activeforeground="#ffffff", relief="flat", bd=0,
                  padx=14, pady=4, font=("Segoe UI", 9, "bold"),
                  cursor="hand2").pack(pady=(0, 12))

    # --- Shutdown ---
    def quit_app(self):
        if self.work_run_started_at is not None:
            self.saved_work = self.current_work_total()
            self.work_run_started_at = None
        x, y = self._current_position()
        save_state(self.saved_work, self.saved_idle, x, y)
        try:
            self.tray_icon.stop()
        except Exception:
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    WorkTimerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

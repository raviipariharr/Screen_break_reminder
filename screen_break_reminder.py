"""
Screen Break Reminder — Pomodoro + 20-20-20 Rule (Green Edition)
------------------------------------------------------------------
A friendly, green-themed desktop app that:
  - Tracks how long you've been on the screen
  - Runs Pomodoro work/break cycles
  - Reminds you every 20 minutes to look 20 feet away for 20 seconds
  - Warns you 30 seconds BEFORE a break starts, so you can wrap up
  - Shows a big, full-green "look away" screen during the eye break,
    with rotating suggestions of other things to do
  - Plays sound alerts for warnings and breaks

Pure Python standard library only (tkinter) so it packages cleanly
into a single .exe with PyInstaller. No internet, no external
dependencies required.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import json
import os
import sys
import random
import threading
from datetime import date

try:
    import winsound
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False


# ---------------------------------------------------------------------------
# Colors — green theme
# ---------------------------------------------------------------------------
COL_BG = "#eef7ee"          # app background, soft green-white
COL_HEADER = "#2e7d32"      # deep green header bar
COL_HEADER_TEXT = "#ffffff"
COL_CARD = "#ffffff"        # card backgrounds
COL_CARD_BORDER = "#c8e6c9"
COL_PRIMARY = "#43a047"     # main green (buttons)
COL_PRIMARY_DARK = "#2e7d32"
COL_PRIMARY_LIGHT = "#e8f5e9"
COL_TEXT = "#1b3a1e"
COL_MUTED = "#5c6b5c"
COL_WARN = "#fb8c00"        # pause / amber
COL_DANGER = "#e53935"      # reset / red (used sparingly)
COL_ACCENT = "#a5d6a7"
COL_EYE_BG = "#2e7d32"      # big full-green eye-rest screen


# ---------------------------------------------------------------------------
# Config / persistence
# ---------------------------------------------------------------------------

def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


DATA_FILE = os.path.join(app_dir(), "screen_break_data.json")

DEFAULTS = {
    "work_minutes": 25,
    "short_break_minutes": 5,
    "long_break_minutes": 15,
    "cycles_before_long_break": 4,
    "rule_interval_minutes": 20,   # 20-20-20: every 20 minutes
    "rule_look_seconds": 20,       # look away for 20 seconds
    "sound_enabled": True,
    "warning_seconds": 30,         # heads-up notice before a break starts
}

EYE_TIPS = [
    "Blink slowly 10 times to re-moisten your eyes.",
    "Stretch your neck gently, side to side.",
    "Roll your shoulders backward a few times.",
    "Take a slow, deep breath in… and out.",
    "Stand up and stretch your arms overhead.",
    "Drink a few sips of water.",
    "Relax your jaw and unclench your shoulders.",
    "Look at a plant, the sky, or anything far away.",
]


def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_data(data):
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def fmt_hms(total_seconds):
    total_seconds = int(total_seconds)
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}h {m:02d}m {s:02d}s"
    return f"{m:02d}m {s:02d}s"


def fmt_mmss(total_seconds):
    total_seconds = max(0, int(total_seconds))
    m, s = divmod(total_seconds, 60)
    return f"{m:02d}:{s:02d}"


def play_chime(kind="break"):
    """Background-thread sound so the UI never freezes.
    kind='warning' -> a single soft, quick beep (heads-up)
    kind='break'   -> a short cheerful ascending chime (break starting)
    """
    def _play():
        if HAS_WINSOUND:
            try:
                if kind == "warning":
                    winsound.Beep(740, 150)
                else:
                    for freq in (587, 740, 880, 1174):
                        winsound.Beep(freq, 140)
            except Exception:
                try:
                    winsound.MessageBeep(winsound.MB_ICONASTERISK)
                except Exception:
                    pass
        else:
            try:
                print("\a")
            except Exception:
                pass
    threading.Thread(target=_play, daemon=True).start()


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------

class ScreenBreakApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Screen Break Reminder 🌿")
        self.root.geometry("460x660")
        self.root.minsize(460, 660)
        self.root.configure(bg=COL_BG)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        stored = load_data()
        self.settings = dict(DEFAULTS)
        self.settings.update(stored.get("settings", {}))

        today = str(date.today())
        if stored.get("last_date") == today:
            self.today_seconds = stored.get("today_seconds", 0)
        else:
            self.today_seconds = 0

        self.state = "idle"          # idle | work | short_break | long_break
        self.remaining = 0
        self.phase_total = self.settings["work_minutes"] * 60
        self.cycle_count = 0
        self.pomodoro_running = False

        self.rule_elapsed = 0
        self.tracking = False
        self.popup_open = False

        # flags so 30-sec warnings fire exactly once per phase
        self.warned_pomodoro = False
        self.warned_eye = False

        self._setup_styles()
        self._build_ui()
        self._refresh_labels()
        self.root.after(1000, self._tick)

    # -- Styles ---------------------------------------------------------------

    def _setup_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("TFrame", background=COL_BG)
        style.configure("Card.TFrame", background=COL_CARD)
        style.configure("Header.TFrame", background=COL_HEADER)

        style.configure("TLabel", background=COL_BG, foreground=COL_TEXT, font=("Segoe UI", 10))
        style.configure("Card.TLabel", background=COL_CARD, foreground=COL_TEXT, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=COL_CARD, foreground=COL_MUTED, font=("Segoe UI", 9))
        style.configure("Header.TLabel", background=COL_HEADER, foreground=COL_HEADER_TEXT,
                         font=("Segoe UI", 17, "bold"))
        style.configure("HeaderSub.TLabel", background=COL_HEADER, foreground="#dff2e0",
                         font=("Segoe UI", 9))
        style.configure("BigTime.TLabel", background=COL_CARD, foreground=COL_PRIMARY_DARK,
                         font=("Segoe UI", 26, "bold"))
        style.configure("Countdown.TLabel", background=COL_CARD, foreground=COL_TEXT,
                         font=("Segoe UI", 40, "bold"))
        style.configure("StateBadge.TLabel", background=COL_PRIMARY_LIGHT, foreground=COL_PRIMARY_DARK,
                         font=("Segoe UI", 11, "bold"), padding=(10, 4))
        style.configure("CardTitle.TLabel", background=COL_CARD, foreground=COL_PRIMARY_DARK,
                         font=("Segoe UI", 11, "bold"))

        # Buttons
        style.configure("Primary.TButton", background=COL_PRIMARY, foreground="white",
                         font=("Segoe UI", 10, "bold"), padding=(16, 8), borderwidth=0)
        style.map("Primary.TButton",
                  background=[("active", COL_PRIMARY_DARK), ("disabled", "#a9d3ac")])

        style.configure("Warn.TButton", background=COL_WARN, foreground="white",
                         font=("Segoe UI", 10, "bold"), padding=(16, 8), borderwidth=0)
        style.map("Warn.TButton",
                  background=[("active", "#ef6c00"), ("disabled", "#ffcc9c")])

        style.configure("Outline.TButton", background=COL_CARD, foreground=COL_PRIMARY_DARK,
                         font=("Segoe UI", 10, "bold"), padding=(16, 8), borderwidth=1)
        style.map("Outline.TButton",
                  background=[("active", COL_PRIMARY_LIGHT)])

        style.configure("Green.Horizontal.TProgressbar", troughcolor=COL_PRIMARY_LIGHT,
                         background=COL_PRIMARY, bordercolor=COL_PRIMARY_LIGHT,
                         lightcolor=COL_PRIMARY, darkcolor=COL_PRIMARY, thickness=10)

    # -- UI ----------------------------------------------------------------

    def _card(self, parent, title=None):
        outer = tk.Frame(parent, bg=COL_CARD_BORDER)
        outer.pack(fill="x", padx=18, pady=8)
        inner = ttk.Frame(outer, style="Card.TFrame")
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        if title:
            ttk.Label(inner, text=title, style="CardTitle.TLabel").pack(
                anchor="w", padx=14, pady=(10, 0))
        return inner

    def _build_ui(self):
        # Header
        header = ttk.Frame(self.root, style="Header.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text="🌿  Screen Break Reminder", style="Header.TLabel").pack(
            anchor="w", padx=18, pady=(16, 0))
        ttk.Label(header, text="Pomodoro focus cycles + the 20-20-20 eye rule",
                  style="HeaderSub.TLabel").pack(anchor="w", padx=18, pady=(2, 16))

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True)

        # Today's screen time card
        c1 = self._card(body, "☀  Today's screen time")
        self.lbl_today = ttk.Label(c1, text="00m 00s", style="BigTime.TLabel")
        self.lbl_today.pack(padx=14, pady=(4, 14))

        # Pomodoro card
        c2 = self._card(body, "🍅  Pomodoro timer")

        self.lbl_state = ttk.Label(c2, text="Idle", style="StateBadge.TLabel")
        self.lbl_state.pack(padx=14, pady=(8, 4))

        self.lbl_countdown = ttk.Label(c2, text="25:00", style="Countdown.TLabel")
        self.lbl_countdown.pack(pady=(2, 6))

        self.progress = ttk.Progressbar(c2, style="Green.Horizontal.TProgressbar",
                                         orient="horizontal", length=360, mode="determinate",
                                         maximum=100, value=0)
        self.progress.pack(padx=20, pady=(0, 8))

        self.lbl_cycles = ttk.Label(c2, text="Completed work sessions: 0", style="Card.TLabel")
        self.lbl_cycles.pack(pady=(0, 10))

        btn_row = ttk.Frame(c2, style="Card.TFrame")
        btn_row.pack(pady=(0, 16))
        self.btn_start = ttk.Button(btn_row, text="▶  Start", style="Primary.TButton",
                                     command=self.start_pomodoro)
        self.btn_start.grid(row=0, column=0, padx=5)
        self.btn_pause = ttk.Button(btn_row, text="⏸  Pause", style="Warn.TButton",
                                     command=self.pause_pomodoro, state="disabled")
        self.btn_pause.grid(row=0, column=1, padx=5)
        self.btn_reset = ttk.Button(btn_row, text="↺  Reset", style="Outline.TButton",
                                     command=self.reset_pomodoro)
        self.btn_reset.grid(row=0, column=2, padx=5)

        # 20-20-20 card
        c3 = self._card(body, "👁  20-20-20 rule")
        ttk.Label(c3, text="Every 20 minutes, look at something 20 feet away for 20 seconds.",
                  style="Muted.TLabel", wraplength=380, justify="left").pack(
            padx=14, pady=(4, 6), anchor="w")
        self.lbl_rule_countdown = ttk.Label(c3, text="Next reminder in: 20:00", style="Card.TLabel")
        self.lbl_rule_countdown.pack(padx=14, pady=(0, 14), anchor="w")

        # Settings + sound toggle
        controls = ttk.Frame(body)
        controls.pack(fill="x", padx=18, pady=(4, 4))

        self.sound_var = tk.BooleanVar(value=self.settings.get("sound_enabled", True))
        sound_chk = ttk.Checkbutton(controls, text="🔊 Play sound on breaks",
                                     variable=self.sound_var, command=self._toggle_sound)
        sound_chk.pack(side="left")

        settings_btn = ttk.Button(controls, text="⚙  Settings", style="Outline.TButton",
                                   command=self.open_settings)
        settings_btn.pack(side="right")

        ttk.Label(body, text="Tip: keep this window open (minimizing is fine) so it can\n"
                              "keep tracking and pop up your reminders.",
                  style="Muted.TLabel", background=COL_BG, justify="center").pack(pady=(6, 14))

    def _toggle_sound(self):
        self.settings["sound_enabled"] = self.sound_var.get()
        self._persist()

    # -- Pomodoro control ----------------------------------------------------

    def start_pomodoro(self):
        if self.state == "idle":
            self.state = "work"
            self.remaining = self.settings["work_minutes"] * 60
            self.phase_total = self.remaining
        self.pomodoro_running = True
        self.tracking = True
        self.warned_pomodoro = False
        self.btn_start.config(state="disabled")
        self.btn_pause.config(state="normal")
        self._refresh_labels()

    def pause_pomodoro(self):
        self.pomodoro_running = False
        self.btn_start.config(state="normal")
        self.btn_pause.config(state="disabled")

    def reset_pomodoro(self):
        self.pomodoro_running = False
        self.state = "idle"
        self.cycle_count = 0
        self.remaining = self.settings["work_minutes"] * 60
        self.phase_total = self.remaining
        self.warned_pomodoro = False
        self.btn_start.config(state="normal")
        self.btn_pause.config(state="disabled")
        self._refresh_labels()

    def _advance_pomodoro_phase(self):
        self.warned_pomodoro = False
        if self.state == "work":
            self.cycle_count += 1
            if self.cycle_count % self.settings["cycles_before_long_break"] == 0:
                self.state = "long_break"
                self.remaining = self.settings["long_break_minutes"] * 60
                msg = (f"Great job — {self.cycle_count} work sessions done today.\n\n"
                       f"Take a long break: {self.settings['long_break_minutes']} minutes.")
            else:
                self.state = "short_break"
                self.remaining = self.settings["short_break_minutes"] * 60
                msg = f"Nice work! Take a short break: {self.settings['short_break_minutes']} minutes."
            self.phase_total = self.remaining
            if self.settings.get("sound_enabled", True):
                play_chime("break")
            self._show_popup("🍃 Break time!", msg)
        else:
            self.state = "work"
            self.remaining = self.settings["work_minutes"] * 60
            self.phase_total = self.remaining
            if self.settings.get("sound_enabled", True):
                play_chime("break")
            self._show_popup("🍅 Back to focus",
                              f"Break's over. Let's focus for {self.settings['work_minutes']} minutes.")
        self._refresh_labels()

    # -- 30-second heads-up warnings ------------------------------------------

    def _show_warning_toast(self, text):
        """A small, borderless, auto-dismissing notice in the corner of
        the screen — doesn't interrupt what you're doing, just a heads-up."""
        if self.settings.get("sound_enabled", True):
            play_chime("warning")

        toast = tk.Toplevel(self.root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        try:
            toast.attributes("-alpha", 0.96)
        except Exception:
            pass
        toast.configure(bg=COL_PRIMARY_DARK)

        w, h = 320, 70
        sw = toast.winfo_screenwidth()
        sh = toast.winfo_screenheight()
        x = sw - w - 24
        y = sh - h - 60
        toast.geometry(f"{w}x{h}+{x}+{y}")

        tk.Label(toast, text=text, bg=COL_PRIMARY_DARK, fg="white",
                 font=("Segoe UI", 10, "bold"), wraplength=290, justify="left").pack(
            expand=True, fill="both", padx=14, pady=10)

        toast.after(4000, lambda: toast.destroy() if toast.winfo_exists() else None)

    # -- 20-20-20 rule --------------------------------------------------------

    def _trigger_202020(self):
        self.warned_eye = False
        self._show_202020_popup()
        self.rule_elapsed = 0

    def _show_202020_popup(self):
        if self.popup_open:
            return
        self.popup_open = True
        seconds = self.settings["rule_look_seconds"]
        tip = random.choice(EYE_TIPS)

        if self.settings.get("sound_enabled", True):
            play_chime("break")

        win = tk.Toplevel(self.root)
        win.title("Eye Break")
        win.configure(bg=COL_EYE_BG)
        win.attributes("-topmost", True)
        try:
            # Try to go full-screen for maximum visibility
            win.attributes("-fullscreen", True)
        except Exception:
            sw = win.winfo_screenwidth()
            sh = win.winfo_screenheight()
            win.geometry(f"{sw}x{sh}+0+0")

        wrap = tk.Frame(win, bg=COL_EYE_BG)
        wrap.pack(expand=True, fill="both")

        tk.Label(wrap, text="👁", bg=COL_EYE_BG, fg="white",
                 font=("Segoe UI", 60)).pack(pady=(60, 0))
        tk.Label(wrap, text="LOOK AWAY FROM YOUR SCREEN", bg=COL_EYE_BG, fg="white",
                 font=("Segoe UI", 30, "bold")).pack(pady=(10, 6))
        tk.Label(wrap, text="Focus on something about 20 feet (6 meters) away.",
                 bg=COL_EYE_BG, fg="#e8f5e9", font=("Segoe UI", 14)).pack(pady=(0, 30))

        lbl_count = tk.Label(wrap, text=str(seconds), bg=COL_EYE_BG, fg="white",
                              font=("Segoe UI", 64, "bold"))
        lbl_count.pack(pady=6)

        tk.Label(wrap, text="While you wait, you could also:", bg=COL_EYE_BG,
                 fg="#c8e6c9", font=("Segoe UI", 11, "italic")).pack(pady=(24, 4))
        tk.Label(wrap, text=tip, bg=COL_EYE_BG, fg="white",
                 font=("Segoe UI", 14, "bold"), wraplength=560, justify="center").pack(pady=(0, 20))

        def countdown(n):
            if not win.winfo_exists():
                return
            if n <= 0:
                self.popup_open = False
                win.destroy()
                return
            lbl_count.config(text=str(n))
            win.after(1000, countdown, n - 1)

        countdown(seconds)

        def on_close():
            self.popup_open = False
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)
        tk.Button(wrap, text="Skip", command=on_close, bg="white", fg=COL_PRIMARY_DARK,
                  font=("Segoe UI", 10, "bold"), relief="flat", padx=18, pady=8).pack(pady=10)
        # allow Escape key to dismiss too
        win.bind("<Escape>", lambda e: on_close())

    # -- Popups -----------------------------------------------------------

    def _show_popup(self, title, message):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(200, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass
        messagebox.showinfo(title, message)

    # -- Settings dialog ----------------------------------------------------

    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.configure(bg=COL_BG)
        win.geometry("380x440")
        win.resizable(False, False)
        win.attributes("-topmost", True)

        tk.Label(win, text="⚙  Settings", bg=COL_BG, fg=COL_PRIMARY_DARK,
                 font=("Segoe UI", 13, "bold")).pack(pady=(14, 10))

        fields = [
            ("Work minutes", "work_minutes"),
            ("Short break minutes", "short_break_minutes"),
            ("Long break minutes", "long_break_minutes"),
            ("Work sessions before long break", "cycles_before_long_break"),
            ("20-20-20 interval (minutes)", "rule_interval_minutes"),
            ("20-20-20 look-away duration (seconds)", "rule_look_seconds"),
            ("Heads-up warning before break (seconds)", "warning_seconds"),
        ]
        form = tk.Frame(win, bg=COL_BG)
        form.pack(padx=16, pady=6, fill="x")
        entries = {}
        for i, (label, key) in enumerate(fields):
            tk.Label(form, text=label, bg=COL_BG, fg=COL_TEXT, font=("Segoe UI", 9),
                     anchor="w", wraplength=230, justify="left").grid(
                row=i, column=0, sticky="w", pady=6)
            e = ttk.Entry(form, width=6, justify="center")
            e.insert(0, str(self.settings[key]))
            e.grid(row=i, column=1, padx=10, pady=6)
            entries[key] = e

        def save_and_close():
            try:
                for key, entry in entries.items():
                    val = int(entry.get())
                    if val <= 0:
                        raise ValueError
                    self.settings[key] = val
            except ValueError:
                messagebox.showerror("Invalid input", "Please enter positive whole numbers only.")
                return
            self._persist()
            self.reset_pomodoro()
            win.destroy()

        ttk.Button(win, text="Save", style="Primary.TButton", command=save_and_close).pack(pady=18)

    # -- Tick loop ------------------------------------------------------------

    def _tick(self):
        today = str(date.today())
        if getattr(self, "_last_date", today) != today:
            self.today_seconds = 0
        self._last_date = today

        warn_secs = self.settings.get("warning_seconds", 30)

        if self.tracking:
            self.today_seconds += 1
            self.rule_elapsed += 1

            rule_remaining = self.settings["rule_interval_minutes"] * 60 - self.rule_elapsed
            if (not self.warned_eye) and 0 < rule_remaining <= warn_secs:
                self.warned_eye = True
                self._show_warning_toast(
                    f"👁  Eye break coming up in {warn_secs} seconds — get ready to look away.")

            if self.rule_elapsed >= self.settings["rule_interval_minutes"] * 60:
                self._trigger_202020()

        if self.pomodoro_running:
            if (self.state == "work"
                    and not self.warned_pomodoro
                    and 0 < self.remaining <= warn_secs):
                self.warned_pomodoro = True
                self._show_warning_toast(
                    f"⏳  Break starting in {warn_secs} seconds — wrap up what you're doing.")

            if self.remaining > 0:
                self.remaining -= 1
            else:
                self._advance_pomodoro_phase()

        self._refresh_labels()
        self._persist()
        self.root.after(1000, self._tick)

    # -- Helpers --------------------------------------------------------------

    def _refresh_labels(self):
        self.lbl_today.config(text=fmt_hms(self.today_seconds))

        state_names = {
            "idle": "Idle — press Start",
            "work": "🍅 Focus / Work",
            "short_break": "☕ Short Break",
            "long_break": "🌴 Long Break",
        }
        self.lbl_state.config(text=state_names.get(self.state, self.state))
        self.lbl_countdown.config(text=fmt_mmss(self.remaining))
        self.lbl_cycles.config(text=f"Completed work sessions: {self.cycle_count}")

        if self.phase_total > 0:
            done = self.phase_total - self.remaining
            pct = max(0, min(100, (done / self.phase_total) * 100))
        else:
            pct = 0
        self.progress["value"] = pct

        rule_remaining = self.settings["rule_interval_minutes"] * 60 - self.rule_elapsed
        suffix = "" if self.tracking else "  (press Start to begin tracking)"
        self.lbl_rule_countdown.config(text=f"Next reminder in: {fmt_mmss(rule_remaining)}{suffix}")

    def _persist(self):
        save_data({
            "settings": self.settings,
            "today_seconds": self.today_seconds,
            "last_date": str(date.today()),
        })

    def on_close(self):
        self._persist()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = ScreenBreakApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
"""
Screen Break Reminder — Full Edition
=======================================
Pomodoro + 20-20-20 screen break companion for Windows with:
  1. Modern dashboard (progress ring, live stats, quick controls)
  2. Dedicated 20-20-20 guided eye-break screen
  3. Native Windows notifications with action buttons (optional)
  4. System tray integration (optional)
  5. Floating "break due" widget + optional full-screen break mode
  6. A short break-activity library (Eyes / Body / Relax / Move)
  7. A statistics page with simple charts and healthy-behavior framing
  8. An optional daily wellbeing score
  9. Presets (Classic / Deep Work / 20-20-20 / Custom) you can save
 10. A comprehensive settings page (Timer / 20-20-20 / Notifications /
     Appearance / Behavior)

Standard library only for the core app; pystray + Pillow (tray icon)
and windows-toasts (native toast buttons) are optional — everything
still works without them, just with simpler in-app equivalents.
"""

import json
import os
import sys
import time
import random
import threading
import queue
import tkinter as tk
from tkinter import ttk, messagebox, colorchooser
from datetime import date, datetime, timedelta

try:
    import winsound
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False

try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

try:
    from windows_toasts import (
        InteractableWindowsToaster, Toast, ToastButton,
        ToastActivatedEventArgs, ToastDuration,
    )
    HAS_NATIVE_TOAST = True
except ImportError:
    HAS_NATIVE_TOAST = False


# ---------------------------------------------------------------------------
# Windows-only helpers (idle time, system theme, start-with-windows) —
# all guarded so the file still runs/parses fine on any platform.
# ---------------------------------------------------------------------------

def get_idle_seconds():
    """Seconds since the last keyboard/mouse input. Returns 0 if unknown
    (non-Windows, or the check failed for any reason)."""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
        millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
        return millis / 1000.0
    except Exception:
        return 0


def get_system_theme():
    if sys.platform != "win32":
        return "light"
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                              r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return "light" if val else "dark"
    except Exception:
        return "light"


def set_start_with_windows(enabled):
    if sys.platform != "win32":
        return
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                              r"Software\Microsoft\Windows\CurrentVersion\Run",
                              0, winreg.KEY_SET_VALUE)
        if enabled:
            if getattr(sys, "frozen", False):
                cmd = f'"{sys.executable}"'
            else:
                cmd = f'"{sys.executable}" "{os.path.abspath(__file__)}"'
            winreg.SetValueEx(key, "ScreenBreakReminder", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, "ScreenBreakReminder")
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Paths / persistence
# ---------------------------------------------------------------------------

def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


DATA_FILE = os.path.join(app_dir(), "screen_break_data.json")
HISTORY_DAYS_KEPT = 30

DEFAULTS = {
    # Timer
    "work_minutes": 25,
    "short_break_minutes": 5,
    "long_break_minutes": 15,
    "cycles_before_long_break": 4,
    "warning_seconds": 30,
    "active_preset": "Classic",
    # 20-20-20
    "eye_rule_enabled": True,
    "rule_interval_minutes": 20,
    "rule_look_seconds": 20,
    "eye_sound_enabled": True,
    # Notifications
    "notifications_enabled": True,
    "notification_sound": True,
    "snooze_minutes": 5,
    "sound_enabled": True,
    # Break style
    "floating_widget_enabled": True,
    "fullscreen_break_mode": False,
    "widget_position": "bottom_right",
    "activity_suggestions_enabled": True,
    # Appearance
    "theme": "light",              # light, dark, system
    "accent_color": "#3FA34D",
    # Behavior
    "start_with_windows": False,
    "minimize_to_tray": True,
    "smart_breaks": False,
    "strict_mode": False,          # False = Gentle, True = Strict
    # Presets
    "custom_presets": {},
}

ACTIVITY_LIBRARY = {
    "EYES": [
        "👁 20-20-20: look 20 feet away for 20 seconds",
        "😌 Blink exercise: blink slowly 10 times",
        "🔭 Look into the distance for 15 seconds",
    ],
    "BODY": [
        "🧍 Neck stretch: gently tilt your head side to side",
        "🤷 Shoulder rolls: roll your shoulders back 10 times",
        "✋ Wrist stretch: extend your arms and stretch your wrists",
    ],
    "RELAX": [
        "🌬 30-second breathing: slow in, slow out",
        "🔲 Box breathing: 4 in, 4 hold, 4 out, 4 hold",
        "🙆 Close your eyes for 20 seconds",
    ],
    "MOVE": [
        "🧎 Stand up and stretch",
        "🚶 Walk around for a minute",
        "💧 Drink a glass of water",
    ],
}
_NON_EYE_ACTIVITIES = (ACTIVITY_LIBRARY["BODY"] + ACTIVITY_LIBRARY["RELAX"]
                        + ACTIVITY_LIBRARY["MOVE"])

PRESETS = {
    "Classic": dict(work_minutes=25, short_break_minutes=5, long_break_minutes=15,
                     cycles_before_long_break=4, rule_interval_minutes=20, rule_look_seconds=20),
    "Deep Work": dict(work_minutes=50, short_break_minutes=10, long_break_minutes=20,
                       cycles_before_long_break=3, rule_interval_minutes=20, rule_look_seconds=20),
    "20-20-20 Focus": dict(work_minutes=20, short_break_minutes=5, long_break_minutes=15,
                            cycles_before_long_break=4, rule_interval_minutes=20, rule_look_seconds=20),
}


def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_data(data):
    """Writes atomically (temp file + rename) so a crash or power loss
    mid-write can never corrupt the saved settings/stats/timer-state —
    the rename is a single filesystem operation, so the file is always
    either the old complete version or the new complete version."""
    try:
        tmp_path = DATA_FILE + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, DATA_FILE)
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


def pct(numerator, denominator, default=100):
    if denominator <= 0:
        return default
    return round(100 * numerator / denominator)


def play_chime(kind="break", enabled=True, emphasize=False):
    """emphasize=True (Strict mode) plays the alert twice in a row for a
    more noticeable, but still non-alarming, reminder."""
    if not enabled:
        return

    def _play_once():
        if HAS_WINSOUND:
            try:
                if kind == "warning":
                    winsound.Beep(740, 150)
                elif kind == "confirm":
                    winsound.Beep(880, 90)
                    winsound.Beep(1174, 140)
                else:
                    for freq in (587, 740, 880, 1174):
                        winsound.Beep(freq, 130)
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

    def _play():
        _play_once()
        if emphasize:
            time.sleep(0.25)
            _play_once()
    threading.Thread(target=_play, daemon=True).start()


# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

THEMES = {
    "light": dict(bg="#FAF8F4", card="#FFFFFF", text="#23262B", muted="#75726C",
                  border="#E9E4DC", ring_track="#F0ECE5", border_soft="#F0ECE5"),
    "dark": dict(bg="#16181C", card="#1F2226", text="#EDEBE7", muted="#9A968E",
                 border="#2C2F35", ring_track="#25282D", border_soft="#25282D"),
}

INDIGO = "#6E62E5"
INDIGO_WASH = {"light": "#EEEBFC", "dark": "#242047"}
AMBER = "#C98A1E"
AMBER_WASH = {"light": "#FBF1DF", "dark": "#3A2E14"}

BASE_STATE_META = {
    "idle":        dict(color="#8A8680", light="#F0ECE5", label="Idle",               emoji="⚪"),
    "focus":       dict(color="#3FA34D", light="#EAF6EC", label="Focus",              emoji="🟢"),
    "break_soon":  dict(color="#C98A1E", light="#FBF1DF", label="Break Soon",         emoji="🟡"),
    "short_break": dict(color="#6E62E5", light="#EEEBFC", label="Short Break",        emoji="🔵"),
    "long_break":  dict(color="#3FA34D", light="#EAF6EC", label="Long Break",         emoji="🌿"),
    "eye_break":   dict(color="#3FA34D", light="#EAF6EC", label="20-20-20 Eye Break", emoji="👀"),
}


def get_state_meta(settings):
    """A copy of the state metadata with the Focus color swapped for the
    user's chosen accent color."""
    meta = {k: dict(v) for k, v in BASE_STATE_META.items()}
    meta["focus"]["color"] = settings.get("accent_color", "#3FA34D")
    return meta


def resolve_theme(settings):
    t = settings.get("theme", "light")
    if t == "system":
        return get_system_theme()
    return t


# ---------------------------------------------------------------------------
# Timer engine
# ---------------------------------------------------------------------------

class TimerEngine:
    """The timer's correctness never depends on how often tick() is
    called or how much time actually passed between calls. Every
    countdown is derived from an absolute wall-clock deadline
    (time.time()), recomputed fresh on every read — so a minimized
    window, a delayed callback, or the system going to sleep and
    waking back up can never cause drift. tick() is just a periodic
    nudge to check "has a deadline passed yet?", not the source of
    truth for how much time is left.
    """

    AWAY_THRESHOLD = 90     # seconds idle before Smart Breaks treats you as away
    GAP_THRESHOLD = 5       # a tick arriving later than this implies a real
                            # gap (sleep, suspend, heavy system load) rather
                            # than normal ~1s polling

    def __init__(self, settings, on_event):
        self.settings = settings
        self.on_event = on_event
        self.reset_all(persisted=None)

    def reset_all(self, persisted=None):
        # Pomodoro / focus timer — optional, off by default.
        self.state = "idle"
        self.running = False
        self.phase_end = None            # epoch seconds; deadline for the current phase
        self.phase_total = self.settings["work_minutes"] * 60
        self._paused_phase_remaining = None

        # 20-20-20 eye-break timer — this is the app's main purpose, so it
        # runs independently of the Pomodoro timer and starts on its own
        # as soon as the app launches (if enabled in Settings). You don't
        # need to start a focus session just to get eye-break reminders.
        self.eye_break_active = False
        self.eye_total = self.settings["rule_interval_minutes"] * 60
        self._paused_eye_remaining = None
        self.eye_running = False
        self.eye_end = None
        if self.settings.get("eye_rule_enabled", True):
            now = time.time()
            self.eye_running = True
            self.eye_end = now + self.eye_total

        self.cycle_count = 0
        self.warned_break = False
        self.warned_eye = False
        self._last_tick_epoch = time.time()

        p = persisted or {}
        self.today_focus_seconds = p.get("today_focus_seconds", 0)
        self.today_break_seconds = p.get("today_break_seconds", 0)
        self.today_screen_seconds = p.get("today_screen_seconds", 0)
        self.eye_completed = p.get("eye_completed", 0)
        self.eye_skipped = p.get("eye_skipped", 0)
        self.short_completed = p.get("short_completed", 0)
        self.short_skipped = p.get("short_skipped", 0)
        self.long_completed = p.get("long_completed", 0)
        self.long_skipped = p.get("long_skipped", 0)
        self.focus_skipped = p.get("focus_skipped", 0)

    # -- live countdowns, always derived from wall-clock deadlines -----------

    @property
    def remaining(self):
        if self.state == "idle":
            return float(self.settings["work_minutes"] * 60)
        if not self.running and self._paused_phase_remaining is not None:
            return self._paused_phase_remaining
        if self.phase_end is None:
            return float(self.phase_total)
        return max(0.0, self.phase_end - time.time())

    @property
    def eye_remaining(self):
        if self.eye_end is None:
            return float(self.settings["rule_interval_minutes"] * 60)
        if not self.eye_running and self._paused_eye_remaining is not None:
            return self._paused_eye_remaining
        return max(0.0, self.eye_end - time.time())

    # -- Pomodoro / focus timer controls (entirely optional) -------------------

    def start(self):
        """Start (or resume) an optional Focus session. Does not affect
        eye-break tracking, which runs independently."""
        now = time.time()
        if self.state == "idle":
            self.state = "focus"
            self.phase_total = self.settings["work_minutes"] * 60
            self.phase_end = now + self.phase_total
        self.running = True
        self._paused_phase_remaining = None
        self.on_event("changed")

    def pause(self):
        if self.running:
            self._paused_phase_remaining = self.remaining if self.state != "idle" else None
        self.running = False
        self.on_event("changed")

    def resume(self):
        if self.state == "idle":
            self.on_event("changed")
            return
        now = time.time()
        self.phase_end = now + (self._paused_phase_remaining
                                 if self._paused_phase_remaining is not None
                                 else self.phase_total)
        self.running = True
        self._paused_phase_remaining = None
        self.on_event("changed")

    def restart(self):
        """Restart the current Focus cycle from a fresh, running session,
        keeping today's stats and eye-break tracking intact."""
        stats = self.stats_snapshot()
        was_eye_running = self.eye_running
        paused_eye_remaining = self._paused_eye_remaining
        self.reset_all(persisted=stats)
        if not was_eye_running:
            # preserve a deliberately-paused eye timer across a Pomodoro restart
            self.eye_running = False
            self._paused_eye_remaining = paused_eye_remaining
        self.start()

    def skip(self):
        if self.state in ("focus", "short_break", "long_break"):
            self._record_skip(self.state)
            self._advance(time.time())
        self.on_event("changed")

    def snooze_break(self, minutes):
        if self.phase_end is not None:
            self.phase_end += minutes * 60
        self.phase_total += minutes * 60
        self.warned_break = False
        self.on_event("changed")

    # -- 20-20-20 eye-break controls (independent of the Pomodoro timer) -------

    def start_eye(self):
        """Turn eye-break tracking on (this is the default/main mode)."""
        now = time.time()
        if not self.eye_running:
            self.eye_total = self.settings["rule_interval_minutes"] * 60
            remaining = (self._paused_eye_remaining if self._paused_eye_remaining is not None
                         else self.eye_total)
            self.eye_end = now + remaining
            self._paused_eye_remaining = None
        self.eye_running = True
        self.on_event("changed")

    def pause_eye(self):
        """Turn eye-break tracking off, e.g. if you don't want reminders
        right now. Independent of the Pomodoro timer."""
        if self.eye_running:
            self._paused_eye_remaining = self.eye_remaining
        self.eye_running = False
        self.warned_eye = False
        self.on_event("changed")

    def snooze_eye(self, minutes):
        self.eye_end = time.time() + minutes * 60
        self.warned_eye = False
        self.eye_break_active = False
        self.on_event("changed")

    def trigger_eye_break_now(self):
        self.warned_eye = False
        self.eye_break_active = True
        self.on_event("eye_break_due")

    def finish_eye_break(self, completed=True):
        self.eye_break_active = False
        if completed:
            self.eye_completed += 1
        else:
            self.eye_skipped += 1
        now = time.time()
        if self.eye_running:
            self.eye_total = self.settings["rule_interval_minutes"] * 60
            self.eye_end = now + self.eye_total
        else:
            self.eye_end = None
        self.on_event("changed")

    # -- per-tick check ---------------------------------------------------------

    def tick(self, idle_seconds=0.0):
        """Call roughly once a second. Late, early, or occasionally-missed
        calls are all fine — correctness comes entirely from comparing
        the stored deadlines to the current wall-clock time, not from
        counting how many times this ran.

        Eye-break tracking and the Pomodoro focus timer are independent:
        either can be running while the other is off."""
        now = time.time()
        delta = now - self._last_tick_epoch
        self._last_tick_epoch = now

        active = self.eye_running or self.running
        if active:
            smart = self.settings.get("smart_breaks", False)
            is_away = smart and idle_seconds >= self.AWAY_THRESHOLD
            gap = delta > self.GAP_THRESHOLD

            if gap:
                # A real gap in execution — system sleep/suspend, the app
                # was heavily delayed, etc. Let the wall-clock deadlines
                # reflect the real time that passed (so, e.g., a break
                # correctly finishes even if you were asleep through it) —
                # just don't count that dead air as tracked screen time.
                pass
            elif is_away:
                # A normal ~1s tick, but you haven't touched the keyboard/
                # mouse in a while and the PC never slept — Smart Breaks
                # pauses whichever timer(s) are active, one second at a
                # time, then resumes exactly where they left off.
                if self.running and self.phase_end is not None:
                    self.phase_end += delta
                if self.eye_running and self.eye_end is not None:
                    self.eye_end += delta
            elif delta > 0:
                self.today_screen_seconds += delta
                if self.running:
                    if self.state == "focus":
                        self.today_focus_seconds += delta
                    elif self.state in ("short_break", "long_break"):
                        self.today_break_seconds += delta

            if (self.eye_running and self.settings.get("eye_rule_enabled", True)
                    and not self.eye_break_active and self.eye_end is not None):
                remain_eye = self.eye_end - now
                warn = self.settings["warning_seconds"]
                if not self.warned_eye and 0 < remain_eye <= warn:
                    self.warned_eye = True
                    self.on_event("eye_warning")
                if remain_eye <= 0:
                    self.warned_eye = False
                    self.eye_break_active = True
                    self.on_event("eye_break_due")

            if (self.running and self.state in ("focus", "short_break", "long_break")
                    and self.phase_end is not None):
                remain = self.phase_end - now
                warn = self.settings["warning_seconds"]
                if self.state == "focus" and not self.warned_break and 0 < remain <= warn:
                    self.warned_break = True
                    self.on_event("break_warning")
                if remain <= 0:
                    self._record_completion(self.state)
                    self._advance(now)

        self.on_event("tick")

    def _record_completion(self, state):
        if state == "short_break":
            self.short_completed += 1
        elif state == "long_break":
            self.long_completed += 1
        # focus completions are tracked via cycle_count in _advance()

    def _record_skip(self, state):
        if state == "short_break":
            self.short_skipped += 1
        elif state == "long_break":
            self.long_skipped += 1
        elif state == "focus":
            self.focus_skipped += 1

    def _advance(self, now=None):
        now = now if now is not None else time.time()
        self.warned_break = False
        if self.state == "focus":
            self.cycle_count += 1
            if self.cycle_count % self.settings["cycles_before_long_break"] == 0:
                self.state = "long_break"
                self.phase_total = self.settings["long_break_minutes"] * 60
            else:
                self.state = "short_break"
                self.phase_total = self.settings["short_break_minutes"] * 60
        else:
            self.state = "focus"
            self.phase_total = self.settings["work_minutes"] * 60
        self.phase_end = now + self.phase_total
        self.on_event("phase_changed", new_state=self.state)

    def display_state(self):
        if self.eye_break_active:
            return "eye_break"
        if self.state == "focus" and self.warned_break:
            return "break_soon"
        return self.state

    # -- persistence: settings/stats are handled elsewhere; this is just
    # the *live* timer state, so a restart or crash can resume seamlessly --

    def to_dict(self):
        return dict(
            state=self.state,
            running=self.running,
            phase_end=self.phase_end,
            phase_total=self.phase_total,
            paused_phase_remaining=self._paused_phase_remaining,
            eye_running=self.eye_running,
            eye_end=self.eye_end,
            eye_total=self.eye_total,
            paused_eye_remaining=self._paused_eye_remaining,
            cycle_count=self.cycle_count,
            warned_break=self.warned_break,
            warned_eye=self.warned_eye,
            saved_at=time.time(),
        )

    def restore(self, d):
        """Recover live timer state saved by a previous run. Safe to call
        with None (nothing to recover -> falls back to the normal
        defaults set by reset_all: Pomodoro idle, eye tracking auto-on)."""
        if not d:
            return
        self.state = d.get("state", "idle")
        self.running = d.get("running", False)
        self.phase_end = d.get("phase_end")
        self.phase_total = d.get("phase_total", self.settings["work_minutes"] * 60)
        self._paused_phase_remaining = d.get("paused_phase_remaining")
        self.eye_running = d.get("eye_running", self.settings.get("eye_rule_enabled", True))
        self.eye_end = d.get("eye_end")
        self.eye_total = d.get("eye_total", self.settings["rule_interval_minutes"] * 60)
        self._paused_eye_remaining = d.get("paused_eye_remaining")
        self.cycle_count = d.get("cycle_count", 0)
        self.warned_break = d.get("warned_break", False)
        self.warned_eye = d.get("warned_eye", False)
        self.eye_break_active = False  # never restore into a mid-flight modal window
        self._last_tick_epoch = time.time()

        now = time.time()
        # If a deadline already passed while the app was closed, crashed,
        # or the system was asleep, reconcile silently — advance the state
        # exactly once and reset the eye cycle — rather than firing
        # notifications for events that (as far as the user is concerned)
        # already happened in the past. This is what keeps a restart from
        # producing duplicate or backdated alerts. The two timers are
        # reconciled independently since either can be running alone.
        if (self.running and self.state in ("focus", "short_break", "long_break")
                and self.phase_end is not None and self.phase_end <= now):
            original_cb = self.on_event
            self.on_event = lambda *a, **k: None
            try:
                self._record_completion(self.state)
                self._advance(now)
            finally:
                self.on_event = original_cb
        if (self.eye_running and self.eye_end is not None and self.eye_end <= now
                and self.settings.get("eye_rule_enabled", True)):
            self.eye_total = self.settings["rule_interval_minutes"] * 60
            self.eye_end = now + self.eye_total
            self.warned_eye = False

    def stats_snapshot(self):
        return dict(
            today_focus_seconds=self.today_focus_seconds,
            today_break_seconds=self.today_break_seconds,
            today_screen_seconds=self.today_screen_seconds,
            eye_completed=self.eye_completed,
            eye_skipped=self.eye_skipped,
            short_completed=self.short_completed,
            short_skipped=self.short_skipped,
            long_completed=self.long_completed,
            long_skipped=self.long_skipped,
            focus_completed=self.cycle_count,
            focus_skipped=self.focus_skipped,
        )

    def wellbeing(self):
        s = self.stats_snapshot()
        eye_pct = pct(s["eye_completed"], s["eye_completed"] + s["eye_skipped"])
        short_pct = pct(s["short_completed"], s["short_completed"] + s["short_skipped"])
        long_pct = pct(s["long_completed"], s["long_completed"] + s["long_skipped"])
        focus_pct = pct(s["focus_completed"], s["focus_completed"] + s["focus_skipped"])
        score = round((eye_pct + short_pct + long_pct + focus_pct) / 4)

        if score >= 90:
            label = "Excellent"
        elif score >= 75:
            label = "Great"
        elif score >= 60:
            label = "Good"
        elif score >= 40:
            label = "Fair"
        else:
            label = "Just getting started"

        breakdown = [
            ("👀", "Eye breaks", eye_pct),
            ("🧘", "Short breaks", short_pct),
            ("🌿", "Long breaks", long_pct),
            ("⏱", "Focus sessions", focus_pct),
        ]
        lowest = min(breakdown, key=lambda x: x[2])
        if score >= 90:
            tip = "💡 You're doing great. Keep this rhythm going!"
        elif lowest[2] < 70:
            tip = f"💡 You're doing well. Try to keep up with your {lowest[1].lower()} a bit more consistently."
        else:
            tip = "💡 You're doing well. Try taking your next long break on time."

        return dict(score=score, label=label, breakdown=breakdown, tip=tip)


# ---------------------------------------------------------------------------
# Native Windows notifications (with graceful fallback)
# ---------------------------------------------------------------------------

class NotificationManager:
    def __init__(self, settings, action_queue):
        self.settings = settings
        self.action_queue = action_queue
        self.toaster = None
        if HAS_NATIVE_TOAST:
            try:
                self.toaster = InteractableWindowsToaster("Screen Break Reminder")
            except Exception:
                self.toaster = None

    def _dispatch(self, action):
        self.action_queue.put(action)

    def notify(self, title, body, actions=None, emphasize=False):
        if not self.settings.get("notifications_enabled", True):
            return
        actions = actions or []

        if self.toaster is not None:
            try:
                toast = Toast([title, body])
                try:
                    toast.duration = ToastDuration.Long if emphasize else ToastDuration.Short
                except Exception:
                    pass
                for label, key in actions:
                    toast.AddAction(ToastButton(label, f"action={key}"))

                def on_activated(args):
                    # This runs on a WinRT/COM callback thread, not the Tk
                    # thread — never touch UI objects here directly. Just
                    # push onto the thread-safe queue and let the Tk main
                    # loop's _poll_actions pick it up. Guard broadly so a
                    # malformed callback can never crash that thread.
                    try:
                        arg = getattr(args, "arguments", "") or ""
                        if arg.startswith("action="):
                            self._dispatch(arg.split("=", 1)[1])
                    except Exception:
                        pass

                toast.on_activated = on_activated
                self.toaster.show_toast(toast)
                if self.settings.get("notification_sound", True):
                    play_chime("break", True, emphasize=emphasize)
                return
            except Exception:
                pass

        self._dispatch(("__fallback_toast__", title, body, actions, emphasize))


# ---------------------------------------------------------------------------
# System tray
# ---------------------------------------------------------------------------

class TrayManager:
    def __init__(self, engine, action_queue, settings):
        self.engine = engine
        self.action_queue = action_queue
        self.settings = settings
        self.icon = None
        self._icon_cache = {}
        if HAS_TRAY:
            self._build()

    def _make_image(self, color_hex):
        if color_hex in self._icon_cache:
            return self._icon_cache[color_hex]
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((4, 4, size - 4, size - 4), fill=color_hex)
        self._icon_cache[color_hex] = img
        return img

    def _status_text(self, item=None):
        disp = self.engine.display_state()
        meta = get_state_meta(self.settings)[disp]
        if disp == "idle":
            if self.engine.eye_running:
                return f"👀 Eye breaks on — next in {fmt_mmss(self.engine.eye_remaining)}"
            return "⚪ Idle — eye breaks paused"
        return f"{meta['emoji']} {meta['label']} — {fmt_mmss(self.engine.remaining)}"

    def _pause_resume_text(self, item=None):
        return "⏸  Pause" if self.engine.running else "▶  Resume"

    def _q(self, action):
        return lambda icon=None, item=None: self.action_queue.put(action)

    def _eye_toggle_text(self, item=None):
        return "⏸  Pause eye-break reminders" if self.engine.eye_running else "▶  Resume eye-break reminders"

    def _toggle_eye(self, icon=None, item=None):
        self.action_queue.put("toggle_eye")

    def _build(self):
        menu = pystray.Menu(
            pystray.MenuItem(self._status_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(self._eye_toggle_text, self._toggle_eye),
            pystray.MenuItem("👀  Take an eye break now", self._q("start_eye_break")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(self._pause_resume_text, self._q("toggle_pause")),
            pystray.MenuItem("⏭  Skip", self._q("skip")),
            pystray.MenuItem("↺  Restart", self._q("restart")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🖥  Open Dashboard", self._q("open_dashboard"), default=True),
            pystray.MenuItem("📊  Statistics", self._q("open_stats")),
            pystray.MenuItem("⚙  Settings", self._q("open_settings")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("✕  Exit", self._q("quit")),
        )
        self.icon = pystray.Icon("screen_break_reminder", self._make_image("#2e7d32"),
                                  "Screen Break Reminder", menu)

    def start(self):
        if self.icon is not None:
            threading.Thread(target=self.icon.run, daemon=True).start()

    def refresh(self):
        if self.icon is None:
            return
        meta = get_state_meta(self.settings)[self.engine.display_state()]
        try:
            self.icon.icon = self._make_image(meta["color"])
            self.icon.title = self._status_text()
        except Exception:
            pass

    def stop(self):
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Progress ring
# ---------------------------------------------------------------------------

class ProgressRing(tk.Canvas):
    def __init__(self, parent, size=220, thickness=14, theme="light", **kw):
        bg = THEMES[theme]["card"]
        super().__init__(parent, width=size, height=size, bg=bg,
                          highlightthickness=0, **kw)
        self.size = size
        self.thickness = thickness
        self.theme = theme
        self.pad = thickness / 2 + 4
        self.arc_id = None
        self.track_id = self.create_oval(self.pad, self.pad, size - self.pad, size - self.pad,
                                          outline=THEMES[theme]["ring_track"], width=thickness)

    def set_theme(self, theme):
        self.theme = theme
        self.configure(bg=THEMES[theme]["card"])
        self.itemconfig(self.track_id, outline=THEMES[theme]["ring_track"])

    def set_progress(self, fraction, color):
        fraction = max(0.0, min(1.0, fraction))
        s, p = self.size, self.pad
        extent = -360 * fraction
        if self.arc_id:
            self.delete(self.arc_id)
            self.arc_id = None
        if fraction > 0:
            self.arc_id = self.create_arc(p, p, s - p, s - p, start=90, extent=extent,
                                           style="arc", outline=color, width=self.thickness)


# ---------------------------------------------------------------------------
# Small bar-chart canvas (used on the Statistics page — no external
# charting library needed)
# ---------------------------------------------------------------------------

class BarChart(tk.Canvas):
    def __init__(self, parent, width=380, height=140, theme="light", **kw):
        super().__init__(parent, width=width, height=height,
                          bg=THEMES[theme]["card"], highlightthickness=0, **kw)
        self.w = width
        self.h = height
        self.theme = theme

    def draw(self, labels, values, color, unit=""):
        self.delete("all")
        if not values:
            return
        max_val = max(values) or 1
        n = len(values)
        margin = 24
        chart_w = self.w - margin * 2
        chart_h = self.h - 34
        bar_w = chart_w / n * 0.55
        gap = chart_w / n

        for i, (label, val) in enumerate(zip(labels, values)):
            x_center = margin + gap * i + gap / 2
            bar_h = (val / max_val) * chart_h if max_val else 0
            x0 = x_center - bar_w / 2
            x1 = x_center + bar_w / 2
            y1 = self.h - 24
            y0 = y1 - bar_h
            self.create_rectangle(x0, y0, x1, y1, fill=color, outline="")
            self.create_text(x_center, self.h - 10, text=label,
                              fill=THEMES[self.theme]["muted"], font=("Segoe UI", 7))
            if val > 0:
                self.create_text(x_center, y0 - 8, text=f"{val:g}{unit}",
                                  fill=THEMES[self.theme]["text"], font=("Segoe UI", 7, "bold"))


# ---------------------------------------------------------------------------
# RoundedCard — a genuinely rounded-corner container (tkinter has no
# native rounded frame, so this draws one on a Canvas and hosts normal
# widgets inside it). Used everywhere a "card" appears in the UI, in
# service of a calmer, more native-feeling Windows design language.
# ---------------------------------------------------------------------------

class RoundedCard(tk.Frame):
    def __init__(self, parent, bg, radius=14, pad=2):
        outer_bg = parent.cget("bg") if "bg" in parent.keys() else "#f4faf5"
        super().__init__(parent, bg=outer_bg)
        self.outer_bg = outer_bg
        self.bg_color = bg
        self.radius = radius
        self.pad = pad
        self.canvas = tk.Canvas(self, bg=outer_bg, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.inner_id = self.canvas.create_window(pad, pad, window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self._redraw())

    @staticmethod
    def _points(x1, y1, x2, y2, r):
        return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
                x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]

    def _redraw(self):
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 4 or h < 4:
            return
        self.canvas.delete("bgrect")
        pts = self._points(1, 1, w - 1, h - 1, self.radius)
        rect = self.canvas.create_polygon(pts, smooth=True, fill=self.bg_color,
                                           outline=self.bg_color, tags="bgrect")
        self.canvas.tag_lower(rect)
        self.canvas.itemconfig(self.inner_id, width=w - 2 * self.pad)

    def set_colors(self, outer_bg, card_bg):
        self.outer_bg = outer_bg
        self.bg_color = card_bg
        self.canvas.configure(bg=outer_bg)
        self.inner.configure(bg=card_bg)
        self._redraw()

    def finalize(self):
        """Call once after all content has been packed into .inner, so
        the card's height matches its content."""
        self.update_idletasks()
        req_h = self.inner.winfo_reqheight()
        self.canvas.configure(height=req_h + 2 * self.pad)
        self._redraw()


# ---------------------------------------------------------------------------
# Switch — a small pill-shaped on/off toggle drawn on a Canvas, matching
# the modern switch controls in the redesigned UI (tkinter's built-in
# Checkbutton can't be restyled to look like this).
# ---------------------------------------------------------------------------

class Switch(tk.Canvas):
    def __init__(self, parent, bg, on_color, off_color, value=False, command=None, width=40, height=23):
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0, cursor="hand2")
        self.on_color = on_color
        self.off_color = off_color
        self.value = value
        self.command = command
        self.w = width
        self.h = height
        self.bind("<Button-1>", self._on_click)
        self._draw()

    def _draw(self):
        self.delete("all")
        r = self.h / 2
        track_color = self.on_color if self.value else self.off_color
        self.create_oval(0, 0, self.h, self.h, fill=track_color, outline=track_color)
        self.create_oval(self.w - self.h, 0, self.w, self.h, fill=track_color, outline=track_color)
        self.create_rectangle(r, 0, self.w - r, self.h, fill=track_color, outline=track_color)
        knob_x = (self.w - r) if self.value else r
        pad = 2.5
        self.create_oval(knob_x - r + pad, pad, knob_x + r - pad, self.h - pad,
                          fill="#FFFFFF", outline="")

    def _on_click(self, event=None):
        self.set(not self.value)
        if self.command:
            self.command(self.value)

    def set(self, value):
        self.value = bool(value)
        self._draw()

    def set_colors(self, bg, on_color, off_color):
        self.on_color = on_color
        self.off_color = off_color
        self.configure(bg=bg)
        self._draw()


# ---------------------------------------------------------------------------
# Main dashboard window
# ---------------------------------------------------------------------------

class Dashboard:
    def __init__(self, root, engine, settings, action_queue, notifier, tray, history):
        self.root = root
        self.engine = engine
        self.settings = settings
        self.action_queue = action_queue
        self.notifier = notifier
        self.tray = tray
        self.history = history  # dict: date-str -> stats snapshot

        self.floating_widget = None
        self.eye_window = None
        self._last_date = str(date.today())

        self.root.title("Screen Break Reminder")
        self.root.geometry("980x760")
        self.root.minsize(760, 600)
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_button)

        self._build_style()
        self._build_ui()
        self._apply_theme()
        self._refresh()
        self._built_once = True

        self.root.after(1000, self._tick_loop)
        self.root.after(200, self._poll_actions)

    # -- theming ------------------------------------------------------------

    def theme_name(self):
        return resolve_theme(self.settings)

    def theme(self):
        return THEMES[self.theme_name()]

    def state_meta(self):
        return get_state_meta(self.settings)

    def _build_style(self):
        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

    def _apply_theme(self):
        t = self.theme()
        accent = self.settings.get("accent_color", "#3FA34D")
        self.root.configure(bg=t["bg"])
        if hasattr(self, "outer_canvas"):
            self.outer_canvas.configure(bg=t["bg"])
        if hasattr(self, "body"):
            self.body.configure(bg=t["bg"])
        s = self.style
        s.configure("TFrame", background=t["bg"])
        s.configure("Card.TFrame", background=t["card"])
        s.configure("TLabel", background=t["bg"], foreground=t["text"], font=("Segoe UI", 10))
        s.configure("Card.TLabel", background=t["card"], foreground=t["text"], font=("Segoe UI", 10))
        s.configure("Muted.TLabel", background=t["card"], foreground=t["muted"], font=("Segoe UI", 9))
        s.configure("Title.TLabel", background=t["bg"], foreground=t["text"], font=("Segoe UI", 18, "bold"))
        s.configure("Sub.TLabel", background=t["bg"], foreground=t["muted"], font=("Segoe UI", 9))
        s.configure("StatNum.TLabel", background=t["card"], foreground=t["text"], font=("Segoe UI", 15, "bold"))
        s.configure("StatLbl.TLabel", background=t["card"], foreground=t["muted"], font=("Segoe UI", 8))
        s.configure("Primary.TButton", background=accent, foreground="white",
                     font=("Segoe UI", 10, "bold"), padding=(14, 8), borderwidth=0)
        s.map("Primary.TButton", background=[("active", accent)])
        s.configure("Outline.TButton", background=t["card"], foreground=t["text"],
                     font=("Segoe UI", 10, "bold"), padding=(14, 8), borderwidth=1)
        s.map("Outline.TButton", background=[("active", t["border"])])

        # A full-dashboard color scheme now touches a lot of individually
        # colored widgets (icon tiles, switches, the streak pill, etc.).
        # Rather than patch each one by hand — fragile and easy to miss a
        # spot — we simply rebuild the body content fresh against the new
        # theme/accent. This is cheap for a dashboard this size and can't
        # drift out of sync the way manual re-coloring can.
        if getattr(self, "_built_once", False) and hasattr(self, "body"):
            for child in self.body.winfo_children():
                child.destroy()
            self._build_body_content()
            self._refresh()

    def _card(self, parent, accent_strip=False):
        """A genuinely rounded, softly elevated card — see RoundedCard."""
        card = RoundedCard(parent, bg=self.theme()["card"], radius=16, pad=2)
        card.pack(fill="x", padx=20, pady=(0, 16))
        strip = None
        if accent_strip:
            strip = tk.Frame(card.inner, bg=self.settings.get("accent_color", "#3FA34D"), height=4)
            strip.pack(fill="x", side="top")
        inner = ttk.Frame(card.inner, style="Card.TFrame")
        inner.pack(fill="both", expand=True)
        return card, inner, strip

    # -- UI ----------------------------------------------------------------

    def _build_scroll_container(self):
        """Wraps the dashboard content in a scrollable area so it degrades
        gracefully if the window is resized smaller than its content,
        instead of clipping (part of a responsive layout)."""
        t = self.theme()
        self.outer_canvas = tk.Canvas(self.root, bg=t["bg"], highlightthickness=0)
        vsb = ttk.Scrollbar(self.root, orient="vertical", command=self.outer_canvas.yview)
        self.outer_canvas.configure(yscrollcommand=vsb.set)
        self.outer_canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.body = tk.Frame(self.outer_canvas, bg=t["bg"])
        self._body_window = self.outer_canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.body.bind("<Configure>", lambda e: self.outer_canvas.configure(
            scrollregion=self.outer_canvas.bbox("all")))
        self.outer_canvas.bind("<Configure>", lambda e: self.outer_canvas.itemconfig(
            self._body_window, width=e.width))

        def _on_wheel(event):
            delta = -1 if event.delta > 0 else 1
            self.outer_canvas.yview_scroll(delta, "units")
        # Only capture the mouse wheel while the cursor is actually over
        # this canvas, so it doesn't hijack scrolling in other windows
        # (Settings, Statistics) that happen to be open at the same time.
        self.outer_canvas.bind("<Enter>", lambda e: self.outer_canvas.bind_all("<MouseWheel>", _on_wheel))
        self.outer_canvas.bind("<Leave>", lambda e: self.outer_canvas.unbind_all("<MouseWheel>"))

    def _build_ui(self):
        self._build_scroll_container()
        self._build_body_content()

    def _build_body_content(self):
        t = self.theme()
        accent = self.settings.get("accent_color", "#3FA34D")

        # -- Compact header: small logo mark, title/subtitle, streak,
        # settings + theme icons. Deliberately not a big colored band —
        # the rings inside the cards are where the visual weight goes.
        header = tk.Frame(self.body, bg=t["bg"])
        header.pack(fill="x", padx=20, pady=(16, 8))

        left = tk.Frame(header, bg=t["bg"])
        left.pack(side="left")
        logo = tk.Canvas(left, width=34, height=34, bg=t["bg"], highlightthickness=0)
        logo.pack(side="left", padx=(0, 10))
        self._draw_logo(logo, accent)
        title_wrap = tk.Frame(left, bg=t["bg"])
        title_wrap.pack(side="left")
        tk.Label(title_wrap, text="Screen Break", bg=t["bg"], fg=t["text"],
                 font=("Segoe UI", 14, "bold")).pack(anchor="w")
        tk.Label(title_wrap, text="Protect your eyes. Stay focused.", bg=t["bg"], fg=t["muted"],
                 font=("Segoe UI", 8)).pack(anchor="w")

        right = tk.Frame(header, bg=t["bg"])
        right.pack(side="right")
        self.streak_pill = tk.Label(right, text="🔥 0 day streak", bg=AMBER_WASH[self.theme_name()],
                                     fg=AMBER, font=("Segoe UI", 9, "bold"), padx=10, pady=5)
        self.streak_pill.pack(side="left", padx=(0, 8))
        tk.Button(right, text="🌙", bg=t["card"], fg=t["muted"], relief="flat", bd=1,
                  font=("Segoe UI", 10), width=3, command=self._on_theme_toggle_icon
                  ).pack(side="left", padx=(0, 6))
        tk.Button(right, text="⚙", bg=t["card"], fg=t["muted"], relief="flat", bd=1,
                  font=("Segoe UI", 10), width=3, command=self._open_full_settings
                  ).pack(side="left")

        # -- two-column layout --
        columns = tk.Frame(self.body, bg=t["bg"])
        columns.pack(fill="both", expand=True, padx=20, pady=(4, 16))
        columns.columnconfigure(0, weight=3, uniform="col")
        columns.columnconfigure(1, weight=2, uniform="col")

        left_col = tk.Frame(columns, bg=t["bg"])
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right_col = tk.Frame(columns, bg=t["bg"])
        right_col.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        # ===== LEFT COLUMN =====

        # -- Eye-break card: this is the app's main purpose, so it comes
        # first and works completely on its own — no focus session needed.
        eye_meta = BASE_STATE_META["eye_break"]
        self.card0, eye_card, self.eye_strip = self._card(left_col, accent_strip=True)
        self.eye_strip.configure(bg=eye_meta["color"])

        eye_head = ttk.Frame(eye_card, style="Card.TFrame")
        eye_head.pack(fill="x", padx=16, pady=(16, 2))
        eye_title_col = ttk.Frame(eye_head, style="Card.TFrame")
        eye_title_col.pack(side="left")
        ttk.Label(eye_title_col, text="Eye Breaks", style="Card.TLabel",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(eye_title_col, text="20-20-20 rule", style="Muted.TLabel").pack(anchor="w")
        self.eye_status_badge = tk.Label(eye_head, text="Active", font=("Segoe UI", 9, "bold"))
        self.eye_status_badge.pack(side="right", anchor="n")

        eye_body = ttk.Frame(eye_card, style="Card.TFrame")
        eye_body.pack(fill="x", padx=16, pady=(10, 6))
        ring_wrap = ttk.Frame(eye_body, style="Card.TFrame")
        ring_wrap.pack(side="left", padx=(0, 18))
        self.eye_ring = ProgressRing(ring_wrap, size=118, thickness=9, theme=self.theme_name())
        self.eye_ring.pack()
        self.eye_time_label = tk.Label(ring_wrap, text="20:00", font=("Segoe UI", 17, "bold"))
        self.eye_time_label.place(relx=0.5, rely=0.42, anchor="center")
        tk.Label(ring_wrap, text="remaining", font=("Segoe UI", 8)).place(
            relx=0.5, rely=0.62, anchor="center")

        eye_copy = ttk.Frame(eye_body, style="Card.TFrame")
        eye_copy.pack(side="left", fill="both", expand=True)
        ttk.Label(eye_copy, text="Next eye break", style="Card.TLabel",
                  font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Label(eye_copy, text="Every 20 minutes, look at something 20 feet away for "
                                  "20 seconds — it gives your eyes a genuine chance to reset.",
                  style="Muted.TLabel", wraplength=260, justify="left").pack(anchor="w", pady=(4, 0))

        eye_btn_row = ttk.Frame(eye_card, style="Card.TFrame")
        eye_btn_row.pack(fill="x", padx=16, pady=(6, 16))
        self.btn_eye_toggle = ttk.Button(eye_btn_row, text="⏸  Pause reminders",
                                          style="Outline.TButton", command=self._on_eye_toggle_button)
        self.btn_eye_toggle.pack(side="left", padx=(0, 8))
        ttk.Button(eye_btn_row, text="👀  Take a break now", style="Primary.TButton",
                   command=lambda: self.action_queue.put("start_eye_break")).pack(side="left")
        self.card0.finalize()

        # -- Focus timer card: entirely optional, off unless you start it.
        self.card1, timer_card, self.timer_strip = self._card(left_col, accent_strip=True)
        focus_head = ttk.Frame(timer_card, style="Card.TFrame")
        focus_head.pack(fill="x", padx=16, pady=(16, 0))
        ttk.Label(focus_head, text="Focus Timer", style="Card.TLabel",
                  font=("Segoe UI", 13, "bold")).pack(side="left")
        tk.Label(focus_head, text="Optional", bg=t["border_soft"], fg=t["muted"],
                 font=("Segoe UI", 8, "bold"), padx=6, pady=1).pack(side="left", padx=(8, 0))
        self.state_badge = tk.Label(focus_head, text="Idle", font=("Segoe UI", 9, "bold"))
        self.state_badge.pack(side="right")

        ring_wrap2 = ttk.Frame(timer_card, style="Card.TFrame")
        ring_wrap2.pack(pady=(10, 4))
        self.ring = ProgressRing(ring_wrap2, size=204, thickness=13, theme=self.theme_name())
        self.ring.pack()
        self.ring_time_label = tk.Label(ring_wrap2, text="25:00", font=("Segoe UI", 30, "bold"))
        self.ring_time_label.place(relx=0.5, rely=0.42, anchor="center")
        self.ring_state_label = tk.Label(ring_wrap2, text="", font=("Segoe UI", 10))
        self.ring_state_label.place(relx=0.5, rely=0.58, anchor="center")

        self.lbl_cycles = ttk.Label(timer_card, text="0 focus sessions completed", style="Card.TLabel")
        self.lbl_cycles.pack(pady=(6, 10))

        btn_row = ttk.Frame(timer_card, style="Card.TFrame")
        btn_row.pack(pady=(0, 18))
        self.btn_pause_resume = ttk.Button(btn_row, text="▶  Start focus session", style="Primary.TButton",
                                            command=lambda: self.action_queue.put("toggle_pause"))
        self.btn_pause_resume.grid(row=0, column=0, padx=4)
        self.btn_skip = ttk.Button(btn_row, text="⏭  Skip", style="Outline.TButton",
                                    command=lambda: self.action_queue.put("skip"))
        self.btn_skip.grid(row=0, column=1, padx=4)
        self.btn_restart = ttk.Button(btn_row, text="↺  Restart", style="Outline.TButton",
                                       command=lambda: self.action_queue.put("restart"))
        self.btn_restart.grid(row=0, column=2, padx=4)
        self.card1.finalize()

        # ===== RIGHT COLUMN =====

        # -- Today's progress: 2x2 icon stat tiles --
        self.card2, stats_card, _ = self._card(right_col)
        head = ttk.Frame(stats_card, style="Card.TFrame")
        head.pack(fill="x", padx=14, pady=(14, 8))
        ttk.Label(head, text="Today's progress", style="Card.TLabel",
                  font=("Segoe UI", 12, "bold")).pack(side="left")

        tiles = ttk.Frame(stats_card, style="Card.TFrame")
        tiles.pack(fill="x", padx=14)
        tiles.columnconfigure(0, weight=1, uniform="tile")
        tiles.columnconfigure(1, weight=1, uniform="tile")

        self.stat_labels = {}
        self.stat_bars = {}
        tile_defs = [
            ("screen", "Screen time", "🖥", "green"),
            ("focus", "Focus time", "⏱", "indigo"),
            ("breaks", "Break time", "☕", "amber"),
            ("eye", "Eye breaks", "👁", "gray"),
        ]
        for i, (key, label, icon, colorkind) in enumerate(tile_defs):
            r, c = divmod(i, 2)
            tile_outer = tk.Frame(tiles, bg=t["border_soft"])
            tile_outer.grid(row=r, column=c, sticky="nsew", padx=4, pady=4)
            tile = tk.Frame(tile_outer, bg=t["card"])
            tile.pack(fill="both", expand=True, padx=1, pady=1)
            icon_bg, icon_fg = self._tile_colors(colorkind)
            icon_lbl = tk.Label(tile, text=icon, bg=icon_bg, fg=icon_fg,
                                 font=("Segoe UI", 10), width=2, height=1)
            icon_lbl.pack(anchor="w", padx=10, pady=(10, 6))
            num = tk.Label(tile, text="—", bg=t["card"], fg=t["text"], font=("Segoe UI", 15, "bold"))
            num.pack(anchor="w", padx=10)
            tk.Label(tile, text=label, bg=t["card"], fg=t["muted"], font=("Segoe UI", 8)).pack(
                anchor="w", padx=10, pady=(0, 8))
            bar_bg = tk.Frame(tile, bg=t["border_soft"], height=4)
            bar_bg.pack(fill="x", padx=10, pady=(0, 10))
            bar_fill = tk.Frame(bar_bg, bg=icon_fg, height=4, width=0)
            bar_fill.place(x=0, y=0, relheight=1)
            self.stat_labels[key] = num
            self.stat_bars[key] = (bar_bg, bar_fill, icon_fg)

        view_stats = tk.Label(stats_card, text="View full statistics  →", bg=t["card"],
                               fg=self.settings.get("accent_color", "#3FA34D"),
                               font=("Segoe UI", 9, "bold"), cursor="hand2")
        view_stats.pack(anchor="w", padx=14, pady=(8, 14))
        view_stats.bind("<Button-1>", lambda e: self._open_stats())
        self.card2.finalize()

        # -- Wellness score --
        self.card_wellness, wellness_card, _ = self._card(right_col)
        ttk.Label(wellness_card, text="Today's wellness", style="Card.TLabel",
                  font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=14, pady=(14, 8))

        wbody = ttk.Frame(wellness_card, style="Card.TFrame")
        wbody.pack(fill="x", padx=14)
        wring_wrap = ttk.Frame(wbody, style="Card.TFrame")
        wring_wrap.pack(side="left", padx=(0, 14))
        self.wellness_ring = ProgressRing(wring_wrap, size=78, thickness=7, theme=self.theme_name())
        self.wellness_ring.pack()
        self.wellness_score_label = tk.Label(wring_wrap, text="—", font=("Segoe UI", 15, "bold"))
        self.wellness_score_label.place(relx=0.5, rely=0.42, anchor="center")
        tk.Label(wring_wrap, text="/ 100", font=("Segoe UI", 7)).place(
            relx=0.5, rely=0.63, anchor="center")

        wcopy = ttk.Frame(wbody, style="Card.TFrame")
        wcopy.pack(side="left", fill="both", expand=True)
        self.wellness_label_top = ttk.Label(wcopy, text="—", style="Card.TLabel",
                                             font=("Segoe UI", 10, "bold"))
        self.wellness_label_top.pack(anchor="w")
        self.wellness_tip_label = ttk.Label(wcopy, text="", style="Muted.TLabel",
                                             wraplength=220, justify="left")
        self.wellness_tip_label.pack(anchor="w", pady=(3, 0))

        self.wellness_indicators = ttk.Frame(wellness_card, style="Card.TFrame")
        self.wellness_indicators.pack(fill="x", padx=14, pady=(12, 14))
        self.wellness_rows = {}
        for key, label, colorkind in [("eye", "Eye breaks", "green"),
                                       ("focus", "Focus sessions", "indigo"),
                                       ("screen", "Screen time", "amber")]:
            row = ttk.Frame(self.wellness_indicators, style="Card.TFrame")
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, bg=t["card"], fg=t["muted"], font=("Segoe UI", 9),
                     width=13, anchor="w").pack(side="left")
            bar_bg = tk.Frame(row, bg=t["border_soft"], height=6)
            bar_bg.pack(side="left", fill="x", expand=True, padx=(0, 8))
            _, fg = self._tile_colors(colorkind)
            bar_fill = tk.Frame(bar_bg, bg=fg, height=6, width=0)
            bar_fill.place(x=0, y=0, relheight=1)
            val_lbl = tk.Label(row, text="—", bg=t["card"], fg=t["text"], font=("Segoe UI", 9, "bold"))
            val_lbl.pack(side="right")
            self.wellness_rows[key] = (bar_bg, bar_fill, val_lbl, fg)
        self.card_wellness.finalize()

        # -- Quick settings --
        self.card3, quick_card, _ = self._card(right_col)
        ttk.Label(quick_card, text="Quick settings", style="Card.TLabel",
                  font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=14, pady=(14, 4))

        self.switches = {}

        def switch_row(parent, label, key, initial, on_change):
            row = ttk.Frame(parent, style="Card.TFrame")
            row.pack(fill="x", padx=14, pady=7)
            tk.Label(row, text=label, bg=t["card"], fg=t["text"], font=("Segoe UI", 10)).pack(side="left")
            sw = Switch(row, bg=t["card"], on_color=self.settings.get("accent_color", "#3FA34D"),
                        off_color=t["border"], value=initial, command=on_change)
            sw.pack(side="right")
            self.switches[key] = sw
            return sw

        switch_row(quick_card, "Eye break reminders", "eye",
                   self.settings.get("eye_rule_enabled", True), self._on_eye_switch)
        switch_row(quick_card, "Focus timer running", "focus",
                   self.engine.running, self._on_focus_switch)
        switch_row(quick_card, "Sound", "sound",
                   self.settings.get("sound_enabled", True),
                   lambda v: self._quick_set_value("sound_enabled", v))
        switch_row(quick_card, "Start with Windows", "startup",
                   self.settings.get("start_with_windows", False), self._on_startup_switch)

        tk.Frame(quick_card, bg=t["border_soft"], height=1).pack(fill="x", padx=14, pady=(6, 10))

        def dropdown_row(parent, label, key, options, current, on_change):
            row = ttk.Frame(parent, style="Card.TFrame")
            row.pack(fill="x", padx=14, pady=6)
            tk.Label(row, text=label, bg=t["card"], fg=t["text"], font=("Segoe UI", 10)).pack(side="left")
            var = tk.StringVar(value=current)
            combo = ttk.Combobox(row, textvariable=var, values=options, width=8,
                                  state="readonly", font=("Segoe UI", 9))
            combo.pack(side="right")
            combo.bind("<<ComboboxSelected>>", lambda e: on_change(var.get()))
            return var

        dropdown_row(quick_card, "Eye break interval", "eye_interval",
                     ["15 min", "20 min", "25 min", "30 min"],
                     f"{self.settings['rule_interval_minutes']} min", self._on_eye_interval_change)
        dropdown_row(quick_card, "Break duration", "look_duration",
                     ["15 sec", "20 sec", "30 sec"],
                     f"{self.settings['rule_look_seconds']} sec", self._on_look_duration_change)
        dropdown_row(quick_card, "Focus duration", "focus_duration",
                     ["15 min", "25 min", "30 min", "50 min"],
                     f"{self.settings['work_minutes']} min", self._on_focus_duration_change)
        self.card3.finalize()

        bottom = tk.Frame(right_col, bg=t["bg"])
        bottom.pack(fill="x", pady=(2, 0))
        hint = "" if HAS_TRAY else "Tray icon needs: pip install pystray pillow"
        if hint:
            tk.Label(ring_wrap, text="remaining", font=("Segoe UI", 8)).place(
    relx=0.5, rely=0.62, anchor="center")

    def _draw_logo(self, canvas, accent):
        canvas.delete("all")
        canvas.create_oval(1, 1, 33, 33, fill=accent, outline=accent)
        canvas.create_line(17, 9, 17, 25, fill="white", width=2, capstyle="round")
        canvas.create_arc(6, 6, 28, 28, start=200, extent=140, style="arc", outline="white", width=2)

    def _tile_colors(self, kind):
        t = self.theme_name()
        if kind == "green":
            return (self.theme()["border_soft"], self.settings.get("accent_color", "#3FA34D"))
        if kind == "indigo":
            return (INDIGO_WASH[t], INDIGO)
        if kind == "amber":
            return (AMBER_WASH[t], AMBER)
        return (self.theme()["border_soft"], self.theme()["muted"])

    # -- toggle / settings handlers --------------------------------------------

    def _quick_set_value(self, key, value):
        self.settings[key] = value
        self._persist()

    def _on_eye_switch(self, enabled):
        self.settings["eye_rule_enabled"] = enabled
        if enabled:
            self.engine.start_eye()
        else:
            self.engine.pause_eye()
        self._persist()
        self._refresh()

    def _on_focus_switch(self, running):
        if running:
            if self.engine.state == "idle":
                self.engine.start()
            elif not self.engine.running:
                self.engine.resume()
        else:
            if self.engine.running:
                self.engine.pause()
        self._persist()
        self._refresh()

    def _on_startup_switch(self, enabled):
        self.settings["start_with_windows"] = enabled
        set_start_with_windows(enabled)
        self._persist()

    def _on_eye_toggle_button(self):
        if self.engine.eye_running:
            self.engine.pause_eye()
            self.settings["eye_rule_enabled"] = False
        else:
            self.engine.start_eye()
            self.settings["eye_rule_enabled"] = True
        if "eye" in getattr(self, "switches", {}):
            self.switches["eye"].set(self.settings["eye_rule_enabled"])
        self._persist()
        self._refresh()

    def _on_theme_toggle_icon(self):
        current = self.settings.get("theme", "light")
        # cycle light -> dark -> light (system is chosen explicitly in Settings)
        self.settings["theme"] = "light" if current == "dark" else "dark"
        self._apply_theme()
        self._persist()

    def _on_eye_interval_change(self, value):
        minutes = int(value.split()[0])
        self.settings["rule_interval_minutes"] = minutes
        self._persist()

    def _on_look_duration_change(self, value):
        seconds = int(value.split()[0])
        self.settings["rule_look_seconds"] = seconds
        self._persist()

    def _on_focus_duration_change(self, value):
        minutes = int(value.split()[0])
        self.settings["work_minutes"] = minutes
        self._persist()

    def _open_full_settings(self):
        SettingsWindow(self.root, self.settings, self.theme(), self.engine, on_save=self._on_settings_saved)

    def _open_stats(self):
        StatisticsWindow(self.root, self.engine, self.history, self.theme(), self.theme_name())

    def _on_settings_saved(self):
        # Keep the live eye-tracking state in sync with whatever the
        # Settings window's "Enable 20-20-20 reminders" checkbox ended up
        # set to, since that's independent of the Pomodoro timer.
        wants_eye = self.settings.get("eye_rule_enabled", True)
        if wants_eye and not self.engine.eye_running:
            self.engine.start_eye()
        elif not wants_eye and self.engine.eye_running:
            self.engine.pause_eye()
        if "eye" in getattr(self, "switches", {}):
            self.switches["eye"].set(wants_eye)
        self._apply_theme()
        self._persist()
        self._refresh()

    # -- close / tray ---------------------------------------------------------

    def _on_close_button(self):
        if HAS_TRAY and self.tray.icon is not None and self.settings.get("minimize_to_tray", True):
            self.root.withdraw()
        else:
            self.action_queue.put("quit")

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(150, lambda: self.root.attributes("-topmost", False))

    # -- action queue polling -----------------------------------------------

    def _poll_actions(self):
        try:
            while True:
                action = self.action_queue.get_nowait()
                self._handle_action(action)
        except queue.Empty:
            pass
        self.root.after(200, self._poll_actions)

    def _handle_action(self, action):
        if isinstance(action, tuple) and action and action[0] == "__fallback_toast__":
            _, title, body, actions, emphasize = action
            CornerToast(self.root, title, body, actions, self.action_queue, self.theme(),
                        self.settings, emphasize=emphasize)
            return

        if action == "toggle_pause":
            if self.engine.state == "idle":
                self.engine.start()
            elif self.engine.running:
                self.engine.pause()
            else:
                self.engine.resume()
        elif action == "skip":
            self.engine.skip()
        elif action == "restart":
            self.engine.restart()
        elif action == "start_eye_break":
            self.engine.trigger_eye_break_now()
        elif action == "toggle_eye":
            if self.engine.eye_running:
                self.engine.pause_eye()
                self.settings["eye_rule_enabled"] = False
            else:
                self.engine.start_eye()
                self.settings["eye_rule_enabled"] = True
            if hasattr(self, "eye_enabled_var"):
                self.eye_enabled_var.set(self.settings["eye_rule_enabled"])
        elif action in ("start_eye_break_go",):
            self.show()
        elif action == "open_dashboard":
            self.show()
        elif action == "open_stats":
            self.show()
            self._open_stats()
        elif action == "open_settings":
            self.show()
            self._open_full_settings()
        elif action == "start_break_now":
            self.show()
        elif action == "snooze_eye":
            self.engine.snooze_eye(self.settings.get("snooze_minutes", 5))
        elif action == "snooze_break":
            self.engine.snooze_break(self.settings.get("snooze_minutes", 5))
        elif action == "skip_break":
            self.engine.skip()
        elif action == "skip_eye":
            self.engine.finish_eye_break(completed=False)
        elif action == "quit":
            self._quit()
        self._refresh()
        if action != "quit":
            self._persist()

    def _quit(self):
        self._archive_today()
        self._persist()
        if self.tray.icon is not None:
            self.tray.stop()
        self.root.after(50, self.root.destroy)

    # -- daily rollover / history ---------------------------------------------

    def _archive_today(self):
        self.history[self._last_date] = self.engine.stats_snapshot()
        # keep only the most recent N days
        if len(self.history) > HISTORY_DAYS_KEPT:
            for k in sorted(self.history.keys())[:-HISTORY_DAYS_KEPT]:
                del self.history[k]

    def _tick_loop(self):
        today = str(date.today())
        if today != self._last_date:
            self._archive_today()
            self.engine.today_focus_seconds = 0
            self.engine.today_break_seconds = 0
            self.engine.today_screen_seconds = 0
            self.engine.eye_completed = 0
            self.engine.eye_skipped = 0
            self.engine.short_completed = 0
            self.engine.short_skipped = 0
            self.engine.long_completed = 0
            self.engine.long_skipped = 0
            self.engine.focus_skipped = 0
            self.engine.cycle_count = 0
            self._last_date = today

        idle = get_idle_seconds() if self.settings.get("smart_breaks", False) else 0.0
        self.engine.tick(idle_seconds=idle)
        self._refresh()
        self._persist()
        self.root.after(1000, self._tick_loop)

    # -- engine event hook ------------------------------------------------------

    def on_engine_event(self, event, **kwargs):
        strict = self.settings.get("strict_mode", False)

        if event == "break_warning":
            secs = self.settings["warning_seconds"]
            # Gentle: snooze + skip. Strict: skip only (no postponing, but
            # never no way out at all).
            actions = [("Skip to break", "skip_break")] if strict else \
                      [("Snooze", "snooze_break"), ("Skip to break", "skip_break")]
            self._toast_or_fallback("⏳ Break coming up",
                                     f"Your break starts in {secs} seconds — wrap up what you're doing.",
                                     actions, emphasize=strict)
        elif event == "eye_warning":
            secs = self.settings["warning_seconds"]
            actions = [("Start now", "start_eye_break")] if strict else \
                      [("Start now", "start_eye_break"), ("Snooze", "snooze_eye")]
            self._toast_or_fallback("👀 Eye break coming up",
                                     f"Time to look away in {secs} seconds — get ready.", actions,
                                     emphasize=strict)
        elif event == "eye_break_due":
            play_chime("break", self.settings.get("eye_sound_enabled", True), emphasize=strict)
            actions = [("Start Break", "start_eye_break_go"), ("Skip", "skip_eye")] if strict else \
                      [("Start Break", "start_eye_break_go"), ("Snooze", "snooze_eye"), ("Skip", "skip_eye")]
            self.notifier.notify("👀 Time for an eye break",
                                  "You've been looking at your screen for a while.\n"
                                  "Look 20 feet away for 20 seconds.", actions=actions, emphasize=strict)
            self._open_eye_break_window()
        elif event == "phase_changed":
            new_state = kwargs.get("new_state")
            play_chime("break", self.settings.get("sound_enabled", True), emphasize=strict)
            # Skip is always offered — Strict mode is about discouraging
            # postponement, never about removing the user's way out.
            actions = [("Start Break", "start_break_now"), ("Skip", "skip_break")]
            if new_state == "long_break":
                self.notifier.notify("🌿 Long Break Time",
                                      f"You've completed {self.engine.cycle_count} focus sessions.\n"
                                      "Step away from your screen and recharge.", actions=actions,
                                      emphasize=strict)
            elif new_state == "short_break":
                self.notifier.notify("🔵 Short Break Time",
                                      "Nice work! Take a short break before the next focus session.",
                                      actions=actions, emphasize=strict)
            else:
                self.notifier.notify("🟢 Back to Focus",
                                      f"Break's over — {self.settings['work_minutes']} minutes of focus time.")
            if new_state in ("short_break", "long_break"):
                self._show_break_widget(new_state)

    def _toast_or_fallback(self, title, body, actions, emphasize=False):
        if self.settings.get("notifications_enabled", True):
            self.notifier.notify(title, body, actions=actions, emphasize=emphasize)
        else:
            CornerToast(self.root, title, body, actions, self.action_queue, self.theme(),
                        self.settings, emphasize=emphasize)

    # -- floating widget / fullscreen break -----------------------------------

    def _show_break_widget(self, state):
        if self.settings.get("fullscreen_break_mode", False):
            FullscreenBreak(self.root, self.engine, state, self.settings, self.action_queue, self.state_meta())
            return
        if not self.settings.get("floating_widget_enabled", True):
            return
        if self.floating_widget is not None:
            try:
                self.floating_widget.destroy_widget()
            except Exception:
                pass
        self.floating_widget = FloatingWidget(self.root, self.engine, state, self.settings,
                                               self.action_queue, self.theme(), self.state_meta())

    def _open_eye_break_window(self):
        if self.eye_window is not None:
            try:
                self.eye_window.destroy()
            except Exception:
                pass
        self.eye_window = EyeBreakWindow(self.root, self.settings, on_finish=self._on_eye_finish)

    def _on_eye_finish(self, completed=True):
        self.engine.finish_eye_break(completed=completed)
        self.eye_window = None
        self._refresh()

    # -- rendering --------------------------------------------------------------

    def _refresh(self):
        t = self.theme()
        meta_all = self.state_meta()
        disp = self.engine.display_state()
        meta = meta_all[disp]

        idle_with_eyes_on = disp == "idle" and self.engine.eye_running
        badge_text = f"{meta['emoji']}  {meta['label']}"
        if idle_with_eyes_on:
            badge_text += "  ·  👀 eye breaks on"
        if hasattr(self, "state_badge"):
            self.state_badge.config(text=badge_text,
                                     bg=meta["light"], fg=meta["color"], padx=10, pady=4,
                                     font=("Segoe UI", 9, "bold"))
        if getattr(self, "timer_strip", None) is not None:
            self.timer_strip.configure(bg=meta["color"])
        if hasattr(self, "ring_time_label"):
            self.ring_time_label.config(text=fmt_mmss(self.engine.remaining), bg=t["card"], fg=t["text"])
            self.ring_state_label.config(text=meta["label"], bg=t["card"], fg=meta["color"])

        if self.engine.phase_total > 0:
            frac = max(0.0, min(1.0, (self.engine.phase_total - self.engine.remaining) / self.engine.phase_total))
        else:
            frac = 0.0
        if hasattr(self, "ring"):
            self.ring.set_progress(frac, meta["color"])

        eye_meta = BASE_STATE_META["eye_break"]
        eye_total = self.settings["rule_interval_minutes"] * 60
        eye_frac = 1 - max(0.0, min(1.0, self.engine.eye_remaining / eye_total)) if eye_total else 0.0
        if hasattr(self, "eye_ring"):
            self.eye_ring.set_progress(eye_frac, eye_meta["color"] if self.engine.eye_running else t["muted"])
        if hasattr(self, "eye_time_label"):
            self.eye_time_label.config(
                text=fmt_mmss(self.engine.eye_remaining) if self.engine.eye_running else "--:--",
                bg=t["card"], fg=t["text"])
        if hasattr(self, "eye_status_badge"):
            if self.engine.eye_running:
                self.eye_status_badge.config(text="Active", bg=eye_meta["light"], fg=eye_meta["color"],
                                              padx=8, pady=2)
                self.btn_eye_toggle.config(text="⏸  Pause reminders")
            else:
                self.eye_status_badge.config(text="Paused", bg=t["border_soft"], fg=t["muted"],
                                              padx=8, pady=2)
                self.btn_eye_toggle.config(text="▶  Resume reminders")
        if "eye" in getattr(self, "switches", {}):
            self.switches["eye"].set(self.engine.eye_running)
        if "focus" in getattr(self, "switches", {}):
            self.switches["focus"].set(self.engine.running)

        if hasattr(self, "lbl_cycles"):
            self.lbl_cycles.config(text=f"{self.engine.cycle_count} focus sessions completed")
        if hasattr(self, "btn_pause_resume"):
            self.btn_pause_resume.config(
                text=("⏸  Pause" if self.engine.running else
                      ("▶  Resume" if self.engine.state != "idle" else "▶  Start focus session")))

        if hasattr(self, "stat_labels"):
            self.stat_labels["screen"].config(text=fmt_hms(self.engine.today_screen_seconds))
            self.stat_labels["focus"].config(text=fmt_hms(self.engine.today_focus_seconds))
            self.stat_labels["breaks"].config(text=fmt_hms(self.engine.today_break_seconds))
            self.stat_labels["eye"].config(text=str(self.engine.eye_completed))

        if hasattr(self, "stat_bars"):
            # Rough daily "goal" references just to give the tiles a sense
            # of scale — purely visual, not a hard target.
            goals = {"screen": 8 * 3600, "focus": 4 * 3600, "breaks": 3600, "eye": 12}
            values = {"screen": self.engine.today_screen_seconds, "focus": self.engine.today_focus_seconds,
                      "breaks": self.engine.today_break_seconds, "eye": self.engine.eye_completed}
            for key, (bar_bg, bar_fill, color) in self.stat_bars.items():
                pct_val = max(0.0, min(1.0, values[key] / goals[key])) if goals[key] else 0.0
                bar_bg.update_idletasks()
                w = max(1, bar_bg.winfo_width())
                bar_fill.place(width=max(2, int(w * pct_val)))

        if hasattr(self, "wellness_score_label"):
            wb = self.engine.wellbeing()
            self.wellness_ring.set_progress(wb["score"] / 100, INDIGO)
            self.wellness_score_label.config(text=str(wb["score"]), bg=t["card"], fg=t["text"])
            self.wellness_label_top.config(text=wb["label"])
            self.wellness_tip_label.config(text=wb["tip"])
            key_map = {"👀": "eye", "🧘": "focus", "⏱": "focus", "🌿": "screen"}
            # map the four wellbeing rows onto the three summary indicators
            lookup = {label: value for _emoji, label, value in wb["breakdown"]}
            row_values = {
                "eye": lookup.get("Eye breaks", 0),
                "focus": lookup.get("Focus sessions", 0),
                "screen": round((lookup.get("Short breaks", 0) + lookup.get("Long breaks", 0)) / 2),
            }
            for key, (bar_bg, bar_fill, val_lbl, color) in self.wellness_rows.items():
                pct_val = max(0, min(100, row_values.get(key, 0)))
                bar_bg.update_idletasks()
                w = max(1, bar_bg.winfo_width())
                bar_fill.place(width=max(2, int(w * pct_val / 100)))
                val_lbl.config(text=f"{pct_val}%", bg=t["card"], fg=t["text"])

        if hasattr(self, "streak_pill"):
            streak = self._compute_streak()
            plural = "" if streak == 1 else "s"
            self.streak_pill.config(text=f"🔥 {streak} day{plural} streak")

        self.tray.refresh()

    def _compute_streak(self):
        """Consecutive days (including today, if there's been any activity
        yet) with at least one completed eye break or focus session."""
        def was_active(stats):
            return (stats.get("eye_completed", 0) + stats.get("focus_completed", 0)
                    + stats.get("short_completed", 0) + stats.get("long_completed", 0)) > 0

        streak = 0
        d = date.today()
        if was_active(self.engine.stats_snapshot()):
            streak = 1
        d -= timedelta(days=1)
        while True:
            stats = self.history.get(str(d))
            if stats and was_active(stats):
                streak += 1
                d -= timedelta(days=1)
            else:
                break
        return streak

    def _persist(self):
        save_data({
            "settings": self.settings,
            "last_date": self._last_date,
            "stats": self.engine.stats_snapshot(),
            "history": self.history,
            "engine_state": self.engine.to_dict(),
        })


# ---------------------------------------------------------------------------
# Corner toast fallback
# ---------------------------------------------------------------------------

class CornerToast:
    def __init__(self, root, title, body, actions, action_queue, theme, settings, emphasize=False):
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-alpha", 0.0)
        except Exception:
            pass
        self.win.configure(bg=theme["card"])

        accent = settings.get("accent_color", "#3FA34D")
        # Gentle: small, quiet, brief. Strict: a little larger, bolder
        # border, stays up longer — still just a corner toast, never
        # blocking the screen.
        w, h = (340, 150) if emphasize else (320, 130)
        title_size = 11 if emphasize else 10
        stay_ms = 12000 if emphasize else 8000

        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        x, y = sw - w - 24, sh - h - 60
        self.win.geometry(f"{w}x{h}+{x}+{y}")

        tk.Frame(self.win, bg=accent, height=6 if emphasize else 4).pack(fill="x", side="top")
        tk.Label(self.win, text=title, bg=theme["card"], fg=theme["text"],
                 font=("Segoe UI", title_size, "bold"), anchor="w", justify="left",
                 wraplength=w - 30).pack(fill="x", padx=14, pady=(10, 2))
        tk.Label(self.win, text=body, bg=theme["card"], fg=theme["muted"],
                 font=("Segoe UI", 9), anchor="w", justify="left",
                 wraplength=w - 30).pack(fill="x", padx=14)

        if actions:
            btn_row = tk.Frame(self.win, bg=theme["card"])
            btn_row.pack(fill="x", padx=10, pady=8)
            for label, key in actions[:2]:
                tk.Button(btn_row, text=label, font=("Segoe UI", 8, "bold"),
                          bg=accent, fg="white", relief="flat", padx=8, pady=3,
                          command=lambda k=key: self._act(action_queue, k)).pack(side="left", padx=4)

        self._fade_in()
        self.win.after(stay_ms, self._fade_out)

    def _act(self, action_queue, key):
        action_queue.put(key)
        self._fade_out()

    def _fade_in(self, alpha=0.0):
        try:
            alpha = min(0.97, alpha + 0.12)
            self.win.attributes("-alpha", alpha)
            if alpha < 0.97:
                self.win.after(15, lambda: self._fade_in(alpha))
        except Exception:
            pass

    def _fade_out(self, alpha=None):
        if not self.win.winfo_exists():
            return
        try:
            if alpha is None:
                alpha = self.win.attributes("-alpha")
            alpha = max(0.0, alpha - 0.12)
            self.win.attributes("-alpha", alpha)
            if alpha > 0:
                self.win.after(15, lambda: self._fade_out(alpha))
            else:
                self.win.destroy()
        except Exception:
            try:
                self.win.destroy()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Floating "break due" widget
# ---------------------------------------------------------------------------

class FloatingWidget:
    POSITIONS = {
        "bottom_right": lambda sw, sh, w, h: (sw - w - 24, sh - h - 60),
        "bottom_left":  lambda sw, sh, w, h: (24, sh - h - 60),
        "top_right":    lambda sw, sh, w, h: (sw - w - 24, 40),
        "top_left":     lambda sw, sh, w, h: (24, 40),
    }

    def __init__(self, root, engine, state, settings, action_queue, theme, state_meta):
        self.root = root
        self.engine = engine
        self.state = state
        self.settings = settings
        self.action_queue = action_queue
        self.theme = theme
        self.alive = True

        meta = state_meta[state]
        strict = settings.get("strict_mode", False)
        show_activity = settings.get("activity_suggestions_enabled", True)
        h = 190 if show_activity else 140
        w = 300
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        try:
            self.win.attributes("-alpha", 0.0)
        except Exception:
            pass
        self.win.configure(bg=theme["card"])

        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        pos_fn = self.POSITIONS.get(settings.get("widget_position", "bottom_right"),
                                     self.POSITIONS["bottom_right"])
        x, y = pos_fn(sw, sh, w, h)
        self.win.geometry(f"{w}x{h}+{x}+{y}")

        tk.Frame(self.win, bg=meta["color"], height=6).pack(fill="x")
        tk.Label(self.win, text=f"{meta['emoji']}  {meta['label']}", bg=theme["card"],
                 fg=meta["color"], font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        self.time_lbl = tk.Label(self.win, text="", bg=theme["card"], fg=theme["text"],
                                  font=("Segoe UI", 20, "bold"))
        self.time_lbl.pack(anchor="w", padx=16)

        if show_activity:
            self.activity_var = tk.StringVar(value=random.choice(_NON_EYE_ACTIVITIES))
            act_row = tk.Frame(self.win, bg=theme["card"])
            act_row.pack(fill="x", padx=16, pady=(4, 0))
            tk.Label(act_row, textvariable=self.activity_var, bg=theme["card"], fg=theme["muted"],
                     font=("Segoe UI", 8), wraplength=210, justify="left", anchor="w").pack(side="left")
            tk.Button(act_row, text="🔄", font=("Segoe UI", 8), relief="flat",
                      bg=theme["card"], fg=theme["muted"], command=self._shuffle_activity
                      ).pack(side="right")

        btn_row = tk.Frame(self.win, bg=theme["card"])
        btn_row.pack(fill="x", padx=14, pady=(8, 12))
        if strict:
            # Strict still needs a genuine way out — just quiet, not a
            # prominent invitation to bail.
            tk.Button(btn_row, text="Exit early", font=("Segoe UI", 7, "underline"),
                      relief="flat", bd=0, bg=theme["card"], fg=theme["muted"],
                      command=self._skip).pack(side="left", padx=4)
        else:
            tk.Button(btn_row, text="Skip", font=("Segoe UI", 8, "bold"), relief="flat",
                      bg=theme["border"], fg=theme["text"], padx=10, pady=4,
                      command=self._skip).pack(side="left", padx=4)
            tk.Button(btn_row, text=f"Snooze {settings.get('snooze_minutes', 5)}m", font=("Segoe UI", 8, "bold"),
                      relief="flat", bg=theme["border"], fg=theme["text"], padx=10, pady=4,
                      command=self._snooze).pack(side="left", padx=4)
        tk.Button(btn_row, text="Dismiss", font=("Segoe UI", 8, "bold"), relief="flat",
                  bg=meta["color"], fg="white", padx=10, pady=4,
                  command=self.destroy_widget).pack(side="right", padx=4)

        self.win.attributes("-topmost", True)
        self._fade_in()
        self._update_loop()

    def _shuffle_activity(self):
        self.activity_var.set(random.choice(_NON_EYE_ACTIVITIES))

    def _skip(self):
        self.action_queue.put("skip")
        self.destroy_widget()

    def _snooze(self):
        self.action_queue.put("snooze_break")
        self.destroy_widget()

    def _update_loop(self):
        if not self.alive or not self.win.winfo_exists():
            return
        self.time_lbl.config(text=fmt_mmss(self.engine.remaining))
        if self.engine.state not in ("short_break", "long_break") or not self.engine.running:
            self.destroy_widget()
            return
        self.win.after(1000, self._update_loop)

    def _fade_in(self, alpha=0.0):
        if not self.win.winfo_exists():
            return
        try:
            alpha = min(0.98, alpha + 0.10)
            self.win.attributes("-alpha", alpha)
            if alpha < 0.98:
                self.win.after(15, lambda: self._fade_in(alpha))
        except Exception:
            pass

    def destroy_widget(self):
        self.alive = False
        if self.win.winfo_exists():
            self._fade_out()

    def _fade_out(self, alpha=None):
        if not self.win.winfo_exists():
            return
        try:
            if alpha is None:
                alpha = self.win.attributes("-alpha")
            alpha = max(0.0, alpha - 0.14)
            self.win.attributes("-alpha", alpha)
            if alpha > 0:
                self.win.after(15, lambda: self._fade_out(alpha))
            else:
                self.win.destroy()
        except Exception:
            try:
                self.win.destroy()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Full-screen break takeover (optional)
# ---------------------------------------------------------------------------

class FullscreenBreak:
    def __init__(self, root, engine, state, settings, action_queue, state_meta):
        self.engine = engine
        self.action_queue = action_queue
        meta = state_meta[state]
        strict = settings.get("strict_mode", False)

        self.win = tk.Toplevel(root)
        self.win.configure(bg=meta["color"])
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-fullscreen", True)
        except Exception:
            sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
            self.win.geometry(f"{sw}x{sh}+0+0")

        wrap = tk.Frame(self.win, bg=meta["color"])
        wrap.pack(expand=True, fill="both")

        tk.Label(wrap, text=meta["emoji"], bg=meta["color"], fg="white",
                 font=("Segoe UI", 60)).pack(pady=(60, 10))
        tk.Label(wrap, text=meta["label"].upper(), bg=meta["color"], fg="white",
                 font=("Segoe UI", 30, "bold")).pack()
        self.time_lbl = tk.Label(wrap, text="", bg=meta["color"], fg="white",
                                  font=("Segoe UI", 60, "bold"))
        self.time_lbl.pack(pady=20)

        if settings.get("activity_suggestions_enabled", True):
            tk.Label(wrap, text=random.choice(_NON_EYE_ACTIVITIES), bg=meta["color"], fg="white",
                     font=("Segoe UI", 13, "bold"), wraplength=560, justify="center").pack(pady=(0, 20))

        # Strict mode is about a stronger nudge, never about trapping the
        # user on this screen — there is always a way out, just quieter
        # in Strict so it isn't an obvious invitation to bail.
        if strict:
            tk.Button(wrap, text="Exit early", command=self._skip, bg=meta["color"], fg="#ffffff",
                      font=("Segoe UI", 9, "underline"), relief="flat", bd=0,
                      activebackground=meta["color"], activeforeground="white").pack(pady=6)
        else:
            tk.Button(wrap, text="Skip break", command=self._skip, bg="white", fg=meta["color"],
                       font=("Segoe UI", 10, "bold"), relief="flat", padx=16, pady=8).pack(pady=6)
        self.win.bind("<Escape>", lambda e: self._skip())

        self._loop()

    def _skip(self):
        try:
            self.win.destroy()
        except Exception:
            pass

    def _loop(self):
        if not self.win.winfo_exists():
            return
        self.time_lbl.config(text=fmt_mmss(self.engine.remaining))
        if self.engine.state not in ("short_break", "long_break"):
            self.win.destroy()
            return
        self.win.after(500, self._loop)


# ---------------------------------------------------------------------------
# 20-20-20 dedicated guided eye-break window
# ---------------------------------------------------------------------------

class EyeBreakWindow:
    def __init__(self, root, settings, on_finish):
        self.settings = settings
        self.on_finish = on_finish
        self.seconds_total = settings["rule_look_seconds"]
        self.deadline = time.time() + self.seconds_total
        self.seconds_left = self.seconds_total
        self._pulse = 0.0
        self._pulse_grow = True
        self._done_naturally = False

        self.win = tk.Toplevel(root)
        self.win.title("20-20-20 Eye Break")
        self.color = BASE_STATE_META["eye_break"]["color"]
        self.win.configure(bg=self.color)
        self.win.attributes("-topmost", True)
        w, h = 620, 520
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f"{w}x{h}+{(sw - w)//2}+{(sh - h)//2}")
        self.win.resizable(False, False)

        tk.Label(self.win, text="👀  20-20-20 Eye Break", bg=self.color, fg="white",
                 font=("Segoe UI", 20, "bold")).pack(pady=(28, 4))
        tk.Label(self.win, text="Look at something about 20 feet (6 meters) away.",
                 bg=self.color, fg="#f1e9fb", font=("Segoe UI", 12)).pack(pady=(0, 6))
        tk.Label(self.win, text="Relax — let your eyes blink naturally.",
                 bg=self.color, fg="#f1e9fb", font=("Segoe UI", 10, "italic")).pack(pady=(0, 10))

        self.canvas = tk.Canvas(self.win, width=260, height=260, bg=self.color, highlightthickness=0)
        self.canvas.pack(pady=6)

        self.tip_label = tk.Label(self.win, text=random.choice(ACTIVITY_LIBRARY["EYES"]), bg=self.color,
                                   fg="white", font=("Segoe UI", 11, "bold"), wraplength=480, justify="center")
        self.tip_label.pack(pady=(14, 6))

        strict = settings.get("strict_mode", False)
        if strict:
            tk.Button(self.win, text="Skip", command=self._skip_now, bg=self.color, fg="white",
                      font=("Segoe UI", 8, "underline"), relief="flat", bd=0,
                      activebackground=self.color, activeforeground="white").pack(pady=6)
        else:
            tk.Button(self.win, text="Skip", command=self._skip_now, bg="white", fg=self.color,
                       font=("Segoe UI", 9, "bold"), relief="flat", padx=14, pady=6).pack(pady=6)
        self.win.bind("<Escape>", lambda e: self._skip_now())
        self.win.protocol("WM_DELETE_WINDOW", self._skip_now)

        self._animate()
        self._countdown()

    def _animate(self):
        if not self.win.winfo_exists():
            return
        self.canvas.delete("all")
        cx, cy = 130, 130
        if self._pulse_grow:
            self._pulse += 0.02
            if self._pulse >= 1.0:
                self._pulse_grow = False
        else:
            self._pulse -= 0.02
            if self._pulse <= 0.0:
                self._pulse_grow = True

        pulse_r = 70 + 10 * self._pulse
        self.canvas.create_oval(cx - pulse_r, cy - pulse_r, cx + pulse_r, cy + pulse_r,
                                 outline="#ffffff", width=2)
        frac = 1 - (self.seconds_left / self.seconds_total) if self.seconds_total else 0
        self.canvas.create_arc(cx - 95, cy - 95, cx + 95, cy + 95, start=90, extent=-360 * frac,
                                style="arc", outline="white", width=6)
        self.canvas.create_text(cx, cy, text=str(max(0, self.seconds_left)), fill="white",
                                 font=("Segoe UI", 46, "bold"))
        self.win.after(40, self._animate)

    def _countdown(self):
        if not self.win.winfo_exists():
            return
        self.seconds_left = max(0, round(self.deadline - time.time()))
        if self.seconds_left <= 0:
            self._done_naturally = True
            self._show_confirmation()
            return
        self.win.after(200, self._countdown)

    def _show_confirmation(self):
        play_chime("confirm", self.settings.get("eye_sound_enabled", True))
        for w in self.win.winfo_children():
            w.destroy()
        tk.Label(self.win, text="✅", bg=self.color, fg="white", font=("Segoe UI", 50)).pack(pady=(90, 10))
        tk.Label(self.win, text="Nice! Your eyes got a reset.", bg=self.color, fg="white",
                 font=("Segoe UI", 18, "bold")).pack()
        tk.Label(self.win, text="Back to it whenever you're ready.", bg=self.color, fg="#f1e9fb",
                 font=("Segoe UI", 10)).pack(pady=(4, 20))
        tk.Button(self.win, text="Continue", command=self._finish_completed, bg="white", fg=self.color,
                  font=("Segoe UI", 10, "bold"), relief="flat", padx=16, pady=8).pack()
        self.win.after(1800, self._finish_completed)

    def _finish_completed(self):
        self._close()
        self.on_finish(completed=True)

    def _skip_now(self):
        self._close()
        self.on_finish(completed=False)

    def _close(self):
        try:
            if self.win.winfo_exists():
                self.win.destroy()
        except Exception:
            pass

    def destroy(self):
        self._close()


# ---------------------------------------------------------------------------
# Statistics + wellbeing page
# ---------------------------------------------------------------------------

class StatisticsWindow:
    def __init__(self, root, engine, history, theme, theme_name):
        self.engine = engine
        self.history = history
        self.theme = theme

        self.win = tk.Toplevel(root)
        self.win.title("Statistics & Wellbeing")
        self.win.configure(bg=theme["bg"])
        self.win.geometry("480x760")
        self.win.resizable(False, False)

        canvas = tk.Canvas(self.win, bg=theme["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.win, orient="vertical", command=canvas.yview)
        frame = tk.Frame(canvas, bg=theme["bg"])
        frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=frame, anchor="nw", width=460)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=10)
        scrollbar.pack(side="right", fill="y")

        s = engine.stats_snapshot()

        # -- Wellbeing score -------------------------------------------------
        wb = engine.wellbeing()
        tk.Label(frame, text="Your daily wellbeing score", bg=theme["bg"], fg=theme["text"],
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=14, pady=(6, 2))
        tk.Label(frame, text="A simple productivity/wellbeing indicator — not a medical measurement.",
                 bg=theme["bg"], fg=theme["muted"], font=("Segoe UI", 8), wraplength=430,
                 justify="left").pack(anchor="w", padx=14, pady=(0, 8))

        score_card = RoundedCard(frame, bg=theme["card"], radius=14)
        score_card.pack(fill="x", padx=14, pady=4)
        sc = score_card.inner
        tk.Label(sc, text=f"{wb['score']} — {wb['label']}", bg=theme["card"],
                 fg=theme["text"], font=("Segoe UI", 22, "bold")).pack(pady=(14, 4))
        for emoji, label, value in wb["breakdown"]:
            row = tk.Frame(sc, bg=theme["card"])
            row.pack(fill="x", padx=20, pady=2)
            tk.Label(row, text=f"{emoji} {label}", bg=theme["card"], fg=theme["muted"],
                     font=("Segoe UI", 9)).pack(side="left")
            tk.Label(row, text=f"{value}%", bg=theme["card"], fg=theme["text"],
                     font=("Segoe UI", 9, "bold")).pack(side="right")
        tk.Label(sc, text=wb["tip"], bg=theme["card"], fg=theme["text"],
                 font=("Segoe UI", 9, "italic"), wraplength=400, justify="left").pack(
            padx=16, pady=(10, 14), anchor="w")
        score_card.finalize()

        # -- Today at a glance -------------------------------------------------
        tk.Label(frame, text="Today at a glance", bg=theme["bg"], fg=theme["text"],
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=14, pady=(18, 6))

        glance_card = RoundedCard(frame, bg=theme["card"], radius=14)
        glance_card.pack(fill="x", padx=14, pady=4)
        glance_grid = tk.Frame(glance_card.inner, bg=theme["card"])
        glance_grid.pack(padx=14, pady=14)
        glance_items = [
            ("Screen time", fmt_hms(s["today_screen_seconds"])),
            ("Focus time", fmt_hms(s["today_focus_seconds"])),
            ("Eye breaks", str(s["eye_completed"])),
            ("Short breaks", str(s["short_completed"])),
            ("Long breaks", str(s["long_completed"])),
            ("Focus sessions", str(s["focus_completed"])),
        ]
        for i, (label, val) in enumerate(glance_items):
            col = tk.Frame(glance_grid, bg=theme["card"])
            col.grid(row=i // 3, column=i % 3, padx=14, pady=6, sticky="w")
            tk.Label(col, text=val, bg=theme["card"], fg=theme["text"],
                     font=("Segoe UI", 13, "bold")).pack(anchor="w")
            tk.Label(col, text=label, bg=theme["card"], fg=theme["muted"],
                     font=("Segoe UI", 8)).pack(anchor="w")
        glance_card.finalize()

        # -- Completed vs skipped, break consistency -----------------------------
        total_completed = s["eye_completed"] + s["short_completed"] + s["long_completed"]
        total_skipped = s["eye_skipped"] + s["short_skipped"] + s["long_skipped"]
        consistency = pct(total_completed, total_completed + total_skipped)

        tk.Label(frame, text="Break consistency", bg=theme["bg"], fg=theme["text"],
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=14, pady=(18, 6))
        consist_card = RoundedCard(frame, bg=theme["card"], radius=14)
        consist_card.pack(fill="x", padx=14, pady=4)
        tk.Label(consist_card.inner, text=f"🌱 You completed {consistency}% of your planned breaks.",
                 bg=theme["card"], fg=theme["text"], font=("Segoe UI", 11, "bold"),
                 wraplength=400, justify="left").pack(padx=16, pady=(14, 4), anchor="w")
        tk.Label(consist_card.inner,
                 text=f"Completed: {total_completed}   ·   Skipped: {total_skipped}",
                 bg=theme["card"], fg=theme["muted"], font=("Segoe UI", 9)).pack(
            padx=16, pady=(0, 14), anchor="w")
        consist_card.finalize()

        # -- Weekly trend chart ------------------------------------------------
        tk.Label(frame, text="Last 7 days — focus time", bg=theme["bg"], fg=theme["text"],
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=14, pady=(18, 6))
        chart_card = RoundedCard(frame, bg=theme["card"], radius=14)
        chart_card.pack(fill="x", padx=14, pady=4)

        days, minutes = self._last_7_days_focus_minutes()
        chart = BarChart(chart_card.inner, width=430, height=150, theme=theme_name)
        chart.pack(padx=8, pady=8)
        chart.draw(days, minutes, "#3FA34D", unit="m")
        chart_card.finalize()

        tk.Label(frame, text="", bg=theme["bg"]).pack(pady=10)  # bottom spacer

    def _last_7_days_focus_minutes(self):
        days, minutes = [], []
        for i in range(6, -1, -1):
            d = date.today() - timedelta(days=i)
            key = str(d)
            if key == str(date.today()):
                secs = self.engine.today_focus_seconds
            else:
                secs = self.history.get(key, {}).get("today_focus_seconds", 0)
            days.append(d.strftime("%a"))
            minutes.append(round(secs / 60))
        return days, minutes


# ---------------------------------------------------------------------------
# Settings window (tabbed: Timer, 20-20-20, Notifications, Appearance, Behavior)
# ---------------------------------------------------------------------------

class SettingsWindow:
    def __init__(self, root, settings, theme, engine, on_save):
        self.settings = settings
        self.engine = engine
        self.on_save = on_save
        self.theme = theme

        self.win = tk.Toplevel(root)
        self.win.title("Settings")
        self.win.configure(bg=theme["bg"])
        self.win.geometry("460x620")
        self.win.attributes("-topmost", True)

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        accent = settings.get("accent_color", "#3FA34D")
        style.configure("TNotebook", background=theme["bg"], borderwidth=0, tabmargins=(6, 8, 6, 0))
        style.configure("TNotebook.Tab", background=theme["border"], foreground=theme["text"],
                         font=("Segoe UI", 9, "bold"), padding=(12, 6), borderwidth=0)
        style.map("TNotebook.Tab",
                  background=[("selected", accent)],
                  foreground=[("selected", "white")])

        notebook = ttk.Notebook(self.win)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.entries = {}
        self.vars = {}

        self._build_timer_tab(notebook)
        self._build_eye_tab(notebook)
        self._build_notif_tab(notebook)
        self._build_appearance_tab(notebook)
        self._build_behavior_tab(notebook)

        btn_frame = tk.Frame(self.win, bg=theme["bg"])
        btn_frame.pack(side="bottom", fill="x", pady=8)
        tk.Button(btn_frame, text="Save", command=self._save, bg=self.settings.get("accent_color", "#3FA34D"),
                  fg="white", font=("Segoe UI", 10, "bold"), relief="flat", padx=18, pady=8).pack()

    # -- helpers --------------------------------------------------------------

    def _tab(self, notebook, title):
        frame = tk.Frame(notebook, bg=self.theme["bg"])
        notebook.add(frame, text=title)
        return frame

    def _number_field(self, parent, label, key):
        row = tk.Frame(parent, bg=self.theme["bg"])
        row.pack(fill="x", padx=10, pady=4)
        tk.Label(row, text=label, bg=self.theme["bg"], fg=self.theme["muted"], font=("Segoe UI", 9),
                 wraplength=260, justify="left", anchor="w").pack(side="left")
        e = ttk.Entry(row, width=6, justify="center")
        e.insert(0, str(self.settings[key]))
        e.pack(side="right")
        self.entries[key] = e

    def _bool_field(self, parent, label, key):
        var = tk.BooleanVar(value=bool(self.settings.get(key, False)))
        ttk.Checkbutton(parent, text=label, variable=var).pack(anchor="w", padx=10, pady=4)
        self.vars[key] = var

    def _dropdown_field(self, parent, label, key, options):
        row = tk.Frame(parent, bg=self.theme["bg"])
        row.pack(fill="x", padx=10, pady=4)
        tk.Label(row, text=label, bg=self.theme["bg"], fg=self.theme["muted"],
                 font=("Segoe UI", 9)).pack(side="left")
        var = tk.StringVar(value=self.settings.get(key, options[0]))
        combo = ttk.Combobox(row, textvariable=var, values=options, width=14, state="readonly")
        combo.pack(side="right")
        self.vars[key] = var
        return combo

    # -- tabs -----------------------------------------------------------------

    def _build_timer_tab(self, notebook):
        f = self._tab(notebook, "Timer")

        tk.Label(f, text="Preset", bg=self.theme["bg"], fg=self.theme["text"],
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(10, 2))
        preset_names = list(PRESETS.keys()) + list(self.settings.get("custom_presets", {}).keys()) + ["Custom"]
        combo = self._dropdown_field(f, "Choose a preset", "active_preset", preset_names)
        combo.bind("<<ComboboxSelected>>", lambda e: self._apply_preset())

        tk.Label(f, text="Timings", bg=self.theme["bg"], fg=self.theme["text"],
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(14, 2))
        self._number_field(f, "Focus duration (min)", "work_minutes")
        self._number_field(f, "Short break duration (min)", "short_break_minutes")
        self._number_field(f, "Long break duration (min)", "long_break_minutes")
        self._number_field(f, "Sessions before long break", "cycles_before_long_break")
        self._number_field(f, "Heads-up warning before break (sec)", "warning_seconds")

        save_row = tk.Frame(f, bg=self.theme["bg"])
        save_row.pack(fill="x", padx=10, pady=(14, 4))
        tk.Label(save_row, text="Save current timings as:", bg=self.theme["bg"],
                 fg=self.theme["muted"], font=("Segoe UI", 9)).pack(side="left")
        self.new_preset_entry = ttk.Entry(save_row, width=14)
        self.new_preset_entry.pack(side="left", padx=6)
        tk.Button(save_row, text="Save preset", command=self._save_custom_preset,
                  bg=self.theme["border"], fg=self.theme["text"], relief="flat",
                  font=("Segoe UI", 8, "bold"), padx=8, pady=3).pack(side="left")

    def _apply_preset(self):
        name = self.vars["active_preset"].get()
        preset = PRESETS.get(name) or self.settings.get("custom_presets", {}).get(name)
        if not preset:
            return  # "Custom" -> leave fields as-is
        for key, val in preset.items():
            if key in self.entries:
                self.entries[key].delete(0, tk.END)
                self.entries[key].insert(0, str(val))

    def _save_custom_preset(self):
        name = self.new_preset_entry.get().strip()
        if not name:
            messagebox.showerror("Name needed", "Give your preset a name first.")
            return
        try:
            preset = dict(
                work_minutes=int(self.entries["work_minutes"].get()),
                short_break_minutes=int(self.entries["short_break_minutes"].get()),
                long_break_minutes=int(self.entries["long_break_minutes"].get()),
                cycles_before_long_break=int(self.entries["cycles_before_long_break"].get()),
                # the 20-20-20 tab's fields aren't on this tab, so fall back to
                # whatever is currently saved in settings for those two values
                rule_interval_minutes=self.settings["rule_interval_minutes"],
                rule_look_seconds=self.settings["rule_look_seconds"],
            )
        except ValueError:
            messagebox.showerror("Invalid input", "Please fix the timing fields first.")
            return
        self.settings.setdefault("custom_presets", {})[name] = preset
        messagebox.showinfo("Saved", f"Preset '{name}' saved.")

    def _build_eye_tab(self, notebook):
        f = self._tab(notebook, "20-20-20")
        self._bool_field(f, "Enable 20-20-20 reminders", "eye_rule_enabled")
        self._number_field(f, "Interval between reminders (min)", "rule_interval_minutes")
        self._number_field(f, "Look-away duration (sec)", "rule_look_seconds")
        self._bool_field(f, "🔊 Play sound", "eye_sound_enabled")

    def _build_notif_tab(self, notebook):
        f = self._tab(notebook, "Notifications")
        self._bool_field(f, "Enable notifications", "notifications_enabled")
        self._bool_field(f, "🔊 Notification sound", "notification_sound")
        self._number_field(f, "Snooze duration (min)", "snooze_minutes")
        tk.Label(f, text="Break reminder style", bg=self.theme["bg"], fg=self.theme["text"],
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(14, 2))
        self._bool_field(f, "Show floating widget when a break starts", "floating_widget_enabled")
        self._bool_field(f, "Full-screen break takeover instead of widget", "fullscreen_break_mode")
        self._dropdown_field(f, "Floating widget position", "widget_position",
                              ["bottom_right", "bottom_left", "top_right", "top_left"])
        self._bool_field(f, "Suggest a quick activity during breaks", "activity_suggestions_enabled")

    def _build_appearance_tab(self, notebook):
        f = self._tab(notebook, "Appearance")
        self._dropdown_field(f, "Theme", "theme", ["light", "dark", "system"])
        row = tk.Frame(f, bg=self.theme["bg"])
        row.pack(fill="x", padx=10, pady=10)
        tk.Label(row, text="Accent color", bg=self.theme["bg"], fg=self.theme["muted"],
                 font=("Segoe UI", 9)).pack(side="left")
        self.accent_preview = tk.Label(row, text="   ", bg=self.settings.get("accent_color", "#3FA34D"))
        self.accent_preview.pack(side="right", padx=(6, 0))
        tk.Button(row, text="Choose…", command=self._pick_accent, relief="flat",
                  bg=self.theme["border"], fg=self.theme["text"], font=("Segoe UI", 8, "bold"),
                  padx=8, pady=3).pack(side="right")

    def _pick_accent(self):
        color = colorchooser.askcolor(color=self.settings.get("accent_color", "#3FA34D"))
        if color and color[1]:
            self.settings["accent_color"] = color[1]
            self.accent_preview.configure(bg=color[1])

    def _build_behavior_tab(self, notebook):
        f = self._tab(notebook, "Behavior")
        self._bool_field(f, "Start with Windows", "start_with_windows")
        self._bool_field(f, "Minimize to tray instead of closing", "minimize_to_tray")
        self._bool_field(f, "Smart breaks (pause tracking when you're away)", "smart_breaks")

        tk.Label(f, text="Break mode", bg=self.theme["bg"], fg=self.theme["text"],
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=10, pady=(16, 2))
        self._dropdown_field(f, "Gentle / Strict", "strict_mode_choice",
                              ["Gentle", "Strict"])
        self.vars["strict_mode_choice"].set("Strict" if self.settings.get("strict_mode") else "Gentle")

        desc = tk.Frame(f, bg=self.theme["card"], highlightbackground=self.theme["border"],
                         highlightthickness=1)
        desc.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(desc, text="🕊  Gentle", bg=self.theme["card"], fg=self.theme["text"],
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(8, 0))
        tk.Label(desc, text="Subtle notifications, a small floating widget, snooze available.",
                 bg=self.theme["card"], fg=self.theme["muted"], font=("Segoe UI", 8),
                 wraplength=380, justify="left").pack(anchor="w", padx=10, pady=(0, 8))
        tk.Label(desc, text="🎯  Strict", bg=self.theme["card"], fg=self.theme["text"],
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(0, 0))
        tk.Label(desc, text="More prominent, longer notifications, no snooze, and pairs well with "
                            "full-screen breaks (Notifications tab) for stronger discipline.",
                 bg=self.theme["card"], fg=self.theme["muted"], font=("Segoe UI", 8),
                 wraplength=380, justify="left").pack(anchor="w", padx=10, pady=(0, 4))
        tk.Label(desc, text="Either way, you can always exit a break early — Strict just makes "
                            "that option quieter, never unavailable.",
                 bg=self.theme["card"], fg=self.theme["muted"], font=("Segoe UI", 8, "italic"),
                 wraplength=380, justify="left").pack(anchor="w", padx=10, pady=(0, 8))

        if not HAS_TRAY:
            tk.Label(f, text="Tray options need: pip install pystray pillow",
                     bg=self.theme["bg"], fg=self.theme["muted"], font=("Segoe UI", 8)).pack(
                anchor="w", padx=10, pady=(10, 0))

    # -- save -------------------------------------------------------------------

    def _save(self):
        try:
            for key, entry in self.entries.items():
                val = int(entry.get())
                if val <= 0:
                    raise ValueError
                self.settings[key] = val
        except ValueError:
            messagebox.showerror("Invalid input", "Please enter positive whole numbers only.")
            return

        for key, var in self.vars.items():
            if key == "strict_mode_choice":
                continue
            self.settings[key] = var.get()

        self.settings["strict_mode"] = self.vars["strict_mode_choice"].get().startswith("Strict")

        prev_start = self.settings.get("_prev_start_with_windows", False)
        if self.settings.get("start_with_windows") != prev_start:
            set_start_with_windows(self.settings.get("start_with_windows", False))
        self.settings["_prev_start_with_windows"] = self.settings.get("start_with_windows", False)

        self.win.destroy()
        self.on_save()


# ---------------------------------------------------------------------------
# App bootstrap
# ---------------------------------------------------------------------------

def main():
    stored = load_data()
    settings = dict(DEFAULTS)
    settings.update(stored.get("settings", {}))

    today = str(date.today())
    persisted_stats = stored.get("stats", {}) if stored.get("last_date") == today else {}
    history = stored.get("history", {})
    if stored.get("last_date") and stored.get("last_date") != today and stored.get("stats"):
        # the app was closed on a previous day without a clean shutdown archive
        history.setdefault(stored["last_date"], stored["stats"])

    action_queue = queue.Queue()
    root = tk.Tk()
    engine_holder = {}

    def on_event(event, **kwargs):
        dash = engine_holder.get("dashboard")
        if dash is not None:
            dash.on_engine_event(event, **kwargs)

    engine = TimerEngine(settings, on_event)
    engine.reset_all(persisted=persisted_stats)
    if stored.get("last_date") == today:
        engine.restore(stored.get("engine_state"))

    notifier = NotificationManager(settings, action_queue)
    tray = TrayManager(engine, action_queue, settings)

    dashboard = Dashboard(root, engine, settings, action_queue, notifier, tray, history)
    engine_holder["dashboard"] = dashboard

    tray.start()
    root.mainloop()


if __name__ == "__main__":
    main()
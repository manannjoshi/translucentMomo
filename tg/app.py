"""Taskbar Glass: clear/translucent taskbar with icons on top.

The C++ TaskbarGlassTAP.dll is injected into explorer.exe and swaps the
taskbar's own XAML "BackgroundFill" brush in-process, so the strip becomes
clear/translucent while the icons, tray, clock and start button stay on top and
fully clickable. Transparency is set from the tray menu panel: 100 = fully
clear, 0 = darkest glass tint.
"""
import os
import queue
import sys
import threading
import time
import tkinter as tk
import winreg

from . import transparency
from .menu import GlassMenu
from .tap import TAPService, find_dll
from .tray import AppTray

REFRESH_MS = 4000
DEFAULT_TRANSPARENCY = 100
TINT_BGR = "0a0a0c"
REG_KEY = r"Software\TaskbarGlass"
REG_VALUE = "TransparencyV2"  # v2: slider 100 = fully clear taskbar
STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE = "TaskbarGlass"
QUIT_LOG = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "TaskbarGlass", "quit.log"
)


def _log_quit(line):
    try:
        os.makedirs(os.path.dirname(QUIT_LOG), exist_ok=True)
        with open(QUIT_LOG, "a", encoding="utf-8") as file:
            file.write(time.strftime("%H:%M:%S ") + line + "\n")
    except Exception:
        pass


def _load_transparency():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as key:
            return int(winreg.QueryValueEx(key, REG_VALUE)[0])
    except Exception:
        return DEFAULT_TRANSPARENCY


def _save_transparency(value):
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_KEY) as key:
            winreg.SetValueEx(key, REG_VALUE, 0, winreg.REG_DWORD, int(value))
    except Exception:
        pass


def _tint_alpha(transparency):
    """Slider 100 = fully clear, slider 0 = darkest glass tint."""
    transparency = max(0, min(100, int(transparency)))
    return round((100 - transparency) * 255 / 100)


def startup_command():
    if getattr(sys, "frozen", False):
        return sys.executable
    return os.path.abspath(sys.argv[0])


def startup_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY) as key:
            winreg.QueryValueEx(key, STARTUP_VALUE)
            return True
    except Exception:
        return False


def set_startup(enable):
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY) as key:
            if enable:
                winreg.SetValueEx(key, STARTUP_VALUE, 0, winreg.REG_SZ, startup_command())
            else:
                try:
                    winreg.DeleteValue(key, STARTUP_VALUE)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


class TaskbarGlassApp:
    def __init__(self):
        self._events = queue.Queue()
        self._tray = None
        self._root = None
        self._menu_win = None
        self._transparency = _load_transparency()
        self._tap = None
        self._tap_ready = False
        self._start_thread = None

    def run(self, quit_after=0):
        self._root = tk.Tk()
        self._root.withdraw()
        self._tray = AppTray(self._events.put)
        try:
            self._tray.start()
        except Exception:
            pass
        transparency.ensure()

        self._tap = TAPService(find_dll())
        self._tap_ready = False
        self._ensure_tap_async()
        self.apply_now()
        if quit_after:
            self._root.after(quit_after, lambda: self._events.put(("quit",)))
        self._root.after(REFRESH_MS, self._refresh)
        try:
            self._root.mainloop()
        finally:
            if self._tray:
                self._tray.stop()
            self._shutdown_tap()

    def _refresh(self):
        if self._process_events():
            return
        # Re-apply periodically so the taskbar heals itself after it is
        # recreated or explorer restarts. The send also reconnects the TAP
        # when explorer was restarted and the pipe went stale.
        self.apply_now()
        if self._root is not None:
            try:
                self._root.after(REFRESH_MS, self._refresh)
            except Exception:
                pass

    def set_transparency(self, value, persist=True):
        value = max(0, min(100, int(value)))
        self._transparency = value
        self.apply_now()
        if persist:
            _save_transparency(value)

    def apply_now(self):
        if self._tap is None:
            return False
        if not self._tap_ready:
            self._ensure_tap_async()
            return False
        alpha = _tint_alpha(self._transparency)
        try:
            if not self._tap.apply(alpha, TINT_BGR):
                self._tap_ready = False
                self._ensure_tap_async()
                return False
            return True
        except Exception:
            self._tap_ready = False
            self._ensure_tap_async()
            return False

    def _ensure_tap_async(self):
        # Reconnect to the persistent service in explorer (or re-inject when
        # explorer restarted) on a worker thread so the app stays responsive
        # while a cold boot still has to spin explorer up.
        if self._start_thread is not None and self._start_thread.is_alive():
            return
        if self._root is None:
            return

        def work():
            try:
                ok = self._tap.start()
            except Exception:
                ok = False
            root = self._root
            if root is not None:
                root.after(0, lambda: self._on_tap_started(ok))

        self._tap_ready = False
        self._start_thread = threading.Thread(target=work, daemon=True)
        self._start_thread.start()

    def _on_tap_started(self, ok):
        self._start_thread = None
        self._tap_ready = bool(ok) and self._tap is not None
        if self._tap_ready:
            try:
                self._tap.watch(os.getpid())
            except Exception:
                pass
            _log_quit("TAP injected and ready")
            self.apply_now()
        else:
            _log_quit("TAP not injected, will retry")

    def restore_stock(self):
        if self._tap is not None:
            try:
                self._tap.restore()
            except Exception:
                pass
        _log_quit("taskbar restored to stock")

    def _shutdown_tap(self):
        if self._tap is None:
            return
        try:
            self._tap.stop()
        except Exception:
            pass
        self._tap = None

    def _process_events(self):
        try:
            while True:
                event = self._events.get_nowait()
                if event == ("quit",):
                    _log_quit("quit event received")
                    self.restore_stock()
                    _log_quit("restore done, destroying root")
                    self._root.destroy()
                    _log_quit("root destroyed")
                    return True
                if event == ("menu",):
                    self._open_menu()
        except queue.Empty:
            pass
        return False

    def _open_menu(self):
        if self._menu_win is not None:
            self._menu_win.close()
            self._menu_win = None
            return
        self._menu_win = GlassMenu(
            self._root,
            self._on_menu_closed,
            transparency=self._transparency,
            on_transparency=self.set_transparency,
            startup_enabled=startup_enabled(),
            on_startup_toggle=set_startup,
        )

    def _on_menu_closed(self):
        self._menu_win = None
        _save_transparency(self._transparency)
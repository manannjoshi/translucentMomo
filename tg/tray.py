"""System tray icon for Translucent momo."""
import base64
import io
import os
import threading
import traceback

import pystray
from PIL import Image

from .logo import LOGO_PNG_B64

ERROR_LOG = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "TaskbarGlass", "error.txt"
)

WM_LBUTTONDBLCLK = 0x0203


class _DoubleClickIcon(pystray.Icon):
    """Systray icon that opens the menu on a double left-click.

    pystray only exposes single left-click (the default menu item); the real
    double-click arrives inside WM_NOTIFY with lParam set to WM_LBUTTONDBLCLK,
    so we handle it explicitly. With no default menu item a single click does
    nothing.
    """

    def __init__(self, *args, on_doubleclick=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._on_doubleclick = on_doubleclick

    def _on_notify(self, wparam, lparam):
        if lparam == WM_LBUTTONDBLCLK:
            if self._on_doubleclick:
                self._on_doubleclick()
            return
        return super()._on_notify(wparam, lparam)


def _log_error(where):
    try:
        os.makedirs(os.path.dirname(ERROR_LOG), exist_ok=True)
        with open(ERROR_LOG, "a", encoding="utf-8") as file:
            file.write(f"[tray:{where}]\n{traceback.format_exc()}\n")
    except Exception:
        pass


def make_icon_image(size=64):
    image = Image.open(io.BytesIO(base64.b64decode(LOGO_PNG_B64))).convert("RGBA")
    return image.resize((size, size), Image.LANCZOS)


class AppTray:
    def __init__(self, emit):
        self.emit = emit
        self._icon = None
        self._thread = None

    def start(self):
        icon = _DoubleClickIcon(
            "taskbar-glass",
            make_icon_image(),
            "Translucent momo",
            pystray.Menu(
                pystray.MenuItem("Menu", self._on_menu),
                pystray.MenuItem("Quit", self._on_quit),
            ),
            on_doubleclick=lambda: self.emit(("menu",)),
        )
        self._icon = icon
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()

    def _thread_main(self):
        try:
            self._icon.run()
        except Exception:
            _log_error("thread crashed")

    def stop(self):
        icon, self._icon = self._icon, None
        if icon:
            try:
                icon.stop()
            except Exception:
                pass

    def _on_quit(self, icon, _item):
        try:
            self.emit(("quit",))
        finally:
            icon.stop()

    def _on_menu(self, _icon, _item):
        try:
            self.emit(("menu",))
        except Exception:
            pass
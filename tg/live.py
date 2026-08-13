"""Live desktop mirror for the taskbar strip.

On Windows 11 the taskbar "band" is a DWM-presented surface that paints on top
of layered windows - neither a transparent accent nor a layered overlay can
ever show the live desktop (animated Wallpaper Engine scenes etc.) inside the
strip.  The one thing that does render above the band is a normal topmost
window.  So this module puts a borderless click-through normal window over each
taskbar strip and draws into it every frame:

    under * (1 - alpha) + samplerows(live desktop) * alpha

where "under" is a snapshot of the strip taken without the overlay (so at low
opacity the taskbar looks normal) and the samplerows are the desktop rows
directly above the taskbar, duplicated down into the strip.  The alpha value is
the transparency slider: at 0 the taskbar looks stock, at 100 the strip shows
the live full screen.
"""
import ctypes
import threading
import time
from ctypes import wintypes

from .windows import (
    class_name as _window_class,
    find_taskbar_windows as _find_taskbar_windows,
)

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
msimg32 = ctypes.WinDLL("msimg32")

WS_POPUP = 0x80000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
SRCCOPY = 0x00CC0020


class Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class BlendFunction(ctypes.Structure):
    _fields_ = [("BlendOp", wintypes.BYTE), ("BlendFlags", wintypes.BYTE),
                ("SourceConstantAlpha", wintypes.BYTE), ("AlphaFormat", wintypes.BYTE)]


class BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", BitmapInfoHeader), ("bmiColors", wintypes.DWORD)]


user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                   wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.RegisterClassW.restype = wintypes.ATOM
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(Rect)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
                                   wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int,
                         wintypes.DWORD]
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.CreateSolidBrush.argtypes = [wintypes.DWORD]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(Rect), wintypes.HBRUSH]
user32.FillRect.restype = ctypes.c_int
msimg32.AlphaBlend.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.HDC,
                               ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, BlendFunction]
msimg32.AlphaBlend.restype = wintypes.BOOL


class _WndClass(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_void_p), ("lpszClassName", wintypes.LPCWSTR)]


_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM)
_win32 = ctypes.windll.kernel32
_win32.GetModuleHandleW.restype = wintypes.HINSTANCE
_win32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


WM_NCHITTEST = 0x0084
HTTRANSPARENT = -1


def _overlay_proc(hwnd, msg, wparam, lparam):
    if msg == WM_NCHITTEST:
        # Pass mouse input straight through to the taskbar underneath.
        return HTTRANSPARENT
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


_OVERLAY_WNDPROC = _WNDPROC(_overlay_proc)


def _register_window_class(name):
    wc = _WndClass(0, ctypes.cast(_OVERLAY_WNDPROC, ctypes.c_void_p).value, 0, 0,
                   _win32.GetModuleHandleW(None), None, None, None, None, name)
    user32.RegisterClassW(ctypes.byref(wc))


def taskbar_strips():
    """Return (rect, hwnd) for each real taskbar window (tray strip)."""
    strips = []
    for hwnd in _find_taskbar_windows():
        if _window_class(hwnd) not in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            continue
        rect = Rect()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            continue
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width > 50 and height >= 8:
            strips.append(((rect.left, rect.top, rect.right, rect.bottom), hwnd))
    return strips


class _Surface:
    """A DIB section plus its memory DC, big enough for the strip."""

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.memdc = None
        self.member = None

    def create(self, anchor_dc):
        self.memdc = gdi32.CreateCompatibleDC(anchor_dc)
        header = BitmapInfo()
        header.bmiHeader.biSize = ctypes.sizeof(BitmapInfoHeader)
        header.bmiHeader.biWidth = self.width
        header.bmiHeader.biHeight = -self.height
        header.bmiHeader.biPlanes = 1
        header.bmiHeader.biBitCount = 32
        header.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        self.member = gdi32.CreateDIBSection(
            anchor_dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0
        )
        gdi32.SelectObject(self.memdc, self.member)

    def destroy(self):
        if self.memdc:
            gdi32.DeleteDC(self.memdc)
            self.memdc = None
        if self.member:
            gdi32.DeleteObject(self.member)
            self.member = None


class _StripOverlay:
    """One normal topmost window over a taskbar strip, mirroring the desktop."""

    def __init__(self, rect, tray_hwnd):
        left, top, right, bottom = rect
        self.rect = (left, top, right, bottom)
        self.tray_hwnd = tray_hwnd
        self.width = right - left
        self.height = bottom - top
        self._visible = False
        self._alpha = 0
        self._solid = False
        self._live = _Surface(self.width, self.height)
        self._under = _Surface(self.width, self.height)
        self.hwnd = None
        name = "TaskbarGlassLiveOverlay"
        _register_window_class(name)
        style = WS_POPUP
        ex_style = (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST)
        self.hwnd = user32.CreateWindowExW(
            ex_style, name, "taskbar-glass-live", style, left, top,
            self.width, self.height, None, None, _win32.GetModuleHandleW(None), None,
        )
        hdc_screen = user32.GetDC(None)
        try:
            self._live.create(hdc_screen)
            self._under.create(hdc_screen)
        finally:
            user32.ReleaseDC(None, hdc_screen)

    def set_alpha(self, alpha):
        self._alpha = max(0, min(255, int(alpha)))

    def set_solid(self, rgb):
        """Fill the blend source with a solid tint colour (no screen capture)."""
        self._solid = True
        bgr = ((rgb & 0xFF) << 16) | (rgb & 0xFF00) | ((rgb >> 16) & 0xFF)
        brush = gdi32.CreateSolidBrush(bgr)
        try:
            rect = Rect(0, 0, self.width, self.height)
            user32.FillRect(self._live.memdc, ctypes.byref(rect), brush)
        finally:
            gdi32.DeleteObject(brush)

    def show(self):
        if not user32.IsWindowVisible(self.tray_hwnd):
            self.hide()
            return
        if not self._visible:
            user32.SetWindowPos(self.hwnd, wintypes.HWND(-1), 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            user32.ShowWindow(self.hwnd, 5)
            self._visible = True

    def hide(self):
        if not self._visible:
            return
        user32.ShowWindow(self.hwnd, 0)
        self._visible = False

    def snapshot_under(self):
        """Store the strip as it looks right now (without the overlay)."""
        left, top, right, bottom = self.rect
        hdc_screen = user32.GetDC(None)
        try:
            gdi32.BitBlt(self._under.memdc, 0, 0, self.width, self.height,
                         hdc_screen, left, top, SRCCOPY)
        finally:
            user32.ReleaseDC(None, hdc_screen)

    def paint(self):
        if not self._visible:
            return
        left, top, right, bottom = self.rect
        source_top = top - self.height
        hdc_wnd = user32.GetDC(self.hwnd)
        hdc_screen = user32.GetDC(None)
        try:
            if not self._solid:
                gdi32.BitBlt(self._live.memdc, 0, 0, self.width, self.height,
                             hdc_screen, left, source_top, SRCCOPY)
            gdi32.BitBlt(hdc_wnd, 0, 0, self.width, self.height,
                         self._under.memdc, 0, 0, SRCCOPY)
            if self._alpha > 0:
                blend = BlendFunction(0, 0, self._alpha, 0)
                msimg32.AlphaBlend(hdc_wnd, 0, 0, self.width, self.height,
                                   self._live.memdc, 0, 0, self.width, self.height,
                                   blend)
        finally:
            user32.ReleaseDC(self.hwnd, hdc_wnd)
            user32.ReleaseDC(None, hdc_screen)

    def destroy(self):
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        self._live.destroy()
        self._under.destroy()


class LiveMirror:
    """Owns the strip overlays and pumps the drawing loop on a background thread."""

    def __init__(self):
        self._overlays = []
        self._thread = None
        self._stop = threading.Event()
        self._opacity = 0

    def start(self):
        if self._overlays or self._thread:
            return
        for rect, tray_hwnd in taskbar_strips():
            try:
                self._overlays.append(_StripOverlay(rect, tray_hwnd))
            except Exception:
                pass
        if not self._overlays:
            return
        for overlay in self._overlays:
            try:
                overlay.snapshot_under()
            except Exception:
                pass
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def set_opacity(self, opacity):
        opacity = max(0, min(100, int(opacity)))
        self._opacity = opacity
        alpha = int(opacity * 255 / 100)
        for overlay in self._overlays:
            try:
                overlay.set_alpha(alpha)
                if alpha <= 0:
                    overlay.hide()
                else:
                    overlay.show()
            except Exception:
                pass

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        for overlay in self._overlays:
            try:
                overlay.destroy()
            except Exception:
                pass
        self._overlays = []

    def _run(self):
        while not self._stop.is_set():
            if self._opacity > 0:
                for overlay in self._overlays:
                    try:
                        overlay.show()
                        overlay.paint()
                    except Exception:
                        pass
            self._stop.wait(0.06)
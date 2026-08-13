"""Find the window handles that make up the Windows taskbar."""
import ctypes
from ctypes import wintypes

TARGET_CLASSES = {
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "XamlExplorerHostIslandWindow",
    "Windows.UI.Composition.DesktopWindowContentBridge",
}

user32 = ctypes.windll.user32

WindowEnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [WindowEnumProc, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.EnumChildWindows.argtypes = [wintypes.HWND, WindowEnumProc, wintypes.LPARAM]
user32.EnumChildWindows.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND


def class_name(hwnd):
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def find_taskbar_windows():
    found = []

    def collect(hwnd, _lparam):
        if class_name(hwnd) in TARGET_CLASSES:
            found.append(hwnd)
        return True

    user32.EnumWindows(WindowEnumProc(collect), 0)
    tray = user32.FindWindowW("Shell_TrayWnd", None)
    if tray:
        user32.EnumChildWindows(tray, WindowEnumProc(collect), 0)
    return found
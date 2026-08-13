"""Window accent control through the undocumented Win32 composition attribute API."""
import ctypes
from ctypes import wintypes

WCA_ACCENT_POLICY = 19
ACCENT_DISABLED = 0
ACCENT_ENABLE_GRADIENT = 1
ACCENT_TRANSPARENTGRADIENT = 2
ACCENT_BLURBEHIND = 3
ACCENT_ACRYLICBLURBEHIND = 4


class AccentPolicy(ctypes.Structure):
    _fields_ = [
        ("accent_state", ctypes.c_int),
        ("accent_flags", ctypes.c_int),
        ("gradient_color", ctypes.c_uint),
        ("animation_id", ctypes.c_int),
    ]


class CompositionAttributeData(ctypes.Structure):
    _fields_ = [
        ("attribute", ctypes.c_int),
        ("p_data", ctypes.c_void_p),
        ("data_size", ctypes.c_size_t),
    ]


user32 = ctypes.windll.user32
user32.SetWindowCompositionAttribute.argtypes = [
    wintypes.HWND, ctypes.POINTER(CompositionAttributeData)
]
user32.SetWindowCompositionAttribute.restype = wintypes.BOOL

DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_NONE = 0
DWMSBT_AUTO = 1
WM_DWMCOMPOSITIONCHANGED = 0x031E

dwmapi = ctypes.windll.dwmapi
dwmapi.DwmSetWindowAttribute.argtypes = [
    wintypes.HWND, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint
]


def _set_backdrop(hwnd, kind):
    value = ctypes.c_int(kind)
    try:
        dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_SYSTEMBACKDROP_TYPE, ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception:
        pass


def _kick_recompose(hwnd):
    # Explorer needs a poke to redraw the taskbar's own material after restore.
    try:
        user32.PostMessageW(hwnd, WM_DWMCOMPOSITIONCHANGED, 1, 0)
    except Exception:
        pass


def apply_transparent(hwnd):
    """Make the taskbar window see-through (clearest translucent state)."""
    policy = AccentPolicy(ACCENT_TRANSPARENTGRADIENT, 2, 0x01000000, 0)
    data = CompositionAttributeData(
        WCA_ACCENT_POLICY,
        ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p),
        ctypes.sizeof(policy),
    )
    ok = bool(user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)))
    return ok


def apply_tint(hwnd, alpha):
    """Translucent tinted taskbar, direct on the taskbar window.

    alpha is 0..255; 1/2 = nearly clear, 255 = opaque dark tint. Applies the
    accent to the taskbar itself (TranslucentTB style) so icons stay usable.
    """
    alpha = max(1, min(255, int(alpha)))
    policy = AccentPolicy(ACCENT_TRANSPARENTGRADIENT, 2, alpha << 24, 0)
    data = CompositionAttributeData(
        WCA_ACCENT_POLICY,
        ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p),
        ctypes.sizeof(policy),
    )
    return bool(user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)))


def apply_normal(hwnd):
    """Restore the taskbar to its stock appearance."""
    policy = AccentPolicy(ACCENT_DISABLED, 0, 0, 0)
    data = CompositionAttributeData(
        WCA_ACCENT_POLICY,
        ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p),
        ctypes.sizeof(policy),
    )
    ok = bool(user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)))
    _set_backdrop(hwnd, DWMSBT_AUTO)
    _kick_recompose(hwnd)
    return ok
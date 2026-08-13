"""Bridge to the injected TaskbarGlassTAP.dll inside explorer.exe.

The launcher creates a named ready event, hooks TaskbarGlassTAP.dll into the
thread that owns Shell_TrayWnd, waits until the TAP reports ready, then talks
to it over a named pipe:

    apply <alpha> <bgrhex>   ->  swap the taskbar BackgroundFill brush
    restore                  ->  restore the original fills
    watch <pid>              ->  restore everything when that process dies
    ping                     ->  pong
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time
import traceback

from ctypes import POINTER, byref, c_void_p, c_int, c_uint, c_ulong

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HANDLE = c_void_p
HHOOK = c_void_p

WH_CALLWNDPROC = 4
WM_NULL = 0x0000

PIPE_ACCESS_DUPLEX = 0x00000003
PIPE_TYPE_BYTE = 0x00000000
PIPE_READMODE_BYTE = 0x00000000
PIPE_WAIT = 0x00000000
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
ERROR_PIPE_BUSY = 231
ERROR_MORE_DATA = 234

READY_EVENT = "TTBG_TAPReady"
PIPE_NAME = r"\\.\pipe\TTBG_TAP"
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258

ERROR_LOG = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")), "TaskbarGlass", "error.txt"
)


def _log_error(where):
    try:
        os.makedirs(os.path.dirname(ERROR_LOG), exist_ok=True)
        with open(ERROR_LOG, "a", encoding="utf-8") as file:
            file.write(f"[tap:{where}]\n{traceback.format_exc()}\n")
    except Exception:
        pass


def _setup_signatures():
    kernel32.CreateEventW.restype = HANDLE
    kernel32.CreateEventW.argtypes = [c_void_p, c_int, c_int, ctypes.c_wchar_p]
    kernel32.WaitForSingleObject.restype = c_ulong
    kernel32.WaitForSingleObject.argtypes = [HANDLE, c_ulong]
    kernel32.CloseHandle.restype = c_int
    kernel32.CloseHandle.argtypes = [HANDLE]
    kernel32.GetModuleHandleW.restype = wt.HINSTANCE
    kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetProcAddress.restype = c_void_p
    kernel32.GetProcAddress.argtypes = [wt.HINSTANCE, ctypes.c_char_p]
    kernel32.CreateFileW.restype = HANDLE
    kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p, c_ulong, c_ulong, c_void_p, c_ulong, c_ulong, HANDLE,
    ]
    kernel32.ReadFile.restype = c_int
    kernel32.ReadFile.argtypes = [HANDLE, c_void_p, c_ulong, POINTER(c_ulong), c_void_p]
    kernel32.WriteFile.restype = c_int
    kernel32.WriteFile.argtypes = [HANDLE, c_void_p, c_ulong, POINTER(c_ulong), c_void_p]
    kernel32.WaitNamedPipeW.restype = c_int
    kernel32.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, c_ulong]

    user32.FindWindowW.restype = wt.HWND
    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.GetWindowThreadProcessId.restype = c_ulong
    user32.GetWindowThreadProcessId.argtypes = [wt.HWND, POINTER(c_ulong)]
    user32.SetWindowsHookExW.restype = HHOOK
    user32.SetWindowsHookExW.argtypes = [c_int, c_void_p, wt.HINSTANCE, c_ulong]
    user32.UnhookWindowsHookEx.restype = c_int
    user32.UnhookWindowsHookEx.argtypes = [HHOOK]
    user32.PostMessageW.restype = c_int
    user32.PostMessageW.argtypes = [wt.HWND, c_uint, c_ulong, c_ulong]


_setup_signatures()


def find_dll():
    """Locate TaskbarGlassTAP.dll next to the app (exe or source tree)."""
    if getattr(sys, "frozen", False):
        candidates = [
            os.path.join(os.path.dirname(sys.executable), "TaskbarGlassTAP.dll"),
            os.path.join(getattr(sys, "_MEIPASS", ""), "TaskbarGlassTAP.dll"),
        ]
    else:
        candidates = [
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "native", "TaskbarGlassTAP.dll"),
        ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


class TAPService:
    def __init__(self, dll_path):
        self.dll_path = dll_path
        self._ready_event = None
        self._hook = None
        self._pipe = HANDLE()
        self._thread_id = 0
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    def start(self, timeout=6.0):
        """Hook the DLL into explorer and wait until the TAP is ready.

        Returns True if the pipe is usable afterwards. The timeout is kept
        short so a cold boot (explorer still starting) fails fast and the
        app's refresh loop retries quickly instead of freezing for minutes.
        """
        try:
            if not self.dll_path or not os.path.isfile(self.dll_path):
                _log_error("dll missing")
                return False

            hwnd = user32.FindWindowW("Shell_TrayWnd", None)
            if not hwnd:
                _log_error("no Shell_TrayWnd")
                return False

            pid = c_ulong()
            self._thread_id = user32.GetWindowThreadProcessId(hwnd, byref(pid))
            if not self._thread_id:
                _log_error("no taskbar thread")
                return False

            self._ready_event = kernel32.CreateEventW(None, True, False, READY_EVENT)
            if not self._ready_event:
                _log_error("create event failed")
                return False

            module = ctypes.WinDLL(self.dll_path)
            hmod = kernel32.GetModuleHandleW(self.dll_path)
            if not hmod:
                _log_error("load dll into self failed")
                return False

            proc = kernel32.GetProcAddress(hmod, b"TapHookWndProc")
            if not proc:
                _log_error("no TapHookWndProc export")
                return False

            # The hook maps the DLL into explorer on the taskbar thread.
            self._hook = user32.SetWindowsHookExW(WH_CALLWNDPROC, proc, hmod, self._thread_id)
            if not self._hook:
                _log_error("hook failed")
                return False

            # Force the hooked thread to dispatch a message so the DLL loads.
            user32.PostMessageW(hwnd, WM_NULL, 0, 0)

            deadline = time.monotonic() + timeout
            while self._ready_event:
                wait_ms = int(max(0.0, deadline - time.monotonic()) * 1000)
                if wait_ms <= 0:
                    break
                status = kernel32.WaitForSingleObject(self._ready_event, min(wait_ms, 1000))
                if status == WAIT_OBJECT_0:
                    break

            # The pipe server may still be starting; give it a moment.
            connected = False
            pipe_deadline = time.monotonic() + 3.0
            while time.monotonic() < pipe_deadline:
                if self._connect_pipe():
                    connected = True
                    break
                time.sleep(0.25)

            if not connected:
                _log_error("pipe connect failed")
                return False

            self._last_error = ""
            return True
        except Exception:
            _log_error("start")
            return False

    def stop(self):
        """Restore the taskbar and release the hook."""
        try:
            self.restore()
        except Exception:
            pass
        self._close_pipe()
        if self._hook:
            try:
                user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None
        if self._ready_event:
            try:
                kernel32.CloseHandle(self._ready_event)
            except Exception:
                pass
            self._ready_event = None

    # -- commands ----------------------------------------------------------

    def apply(self, alpha, bgr_hex):
        alpha = max(0, min(255, int(alpha)))
        return self._send(f"apply {alpha} {bgr_hex}")

    def restore(self):
        self._send("restore")

    def watch(self, pid):
        self._send(f"watch {int(pid)}")

    def ping(self):
        return self._send("ping", expect_response=4) == b"pong"

    # -- internals ---------------------------------------------------------

    def _connect_pipe(self):
        handle = kernel32.CreateFileW(
            PIPE_NAME,
            GENERIC_READ | GENERIC_WRITE,
            0,
            None,
            OPEN_EXISTING,
            0,
            HANDLE(),
        )
        if handle:
            self._pipe = handle
            return True

        err = ctypes.get_last_error()
        if err == ERROR_PIPE_BUSY:
            if kernel32.WaitNamedPipeW(PIPE_NAME, 1000):
                handle = kernel32.CreateFileW(
                    PIPE_NAME,
                    GENERIC_READ | GENERIC_WRITE,
                    0,
                    None,
                    OPEN_EXISTING,
                    0,
                    HANDLE(),
                )
                if handle:
                    self._pipe = handle
                    return True
        return False

    def _close_pipe(self):
        if self._pipe:
            try:
                kernel32.CloseHandle(self._pipe)
            except Exception:
                pass
            self._pipe = HANDLE()

    def _send(self, line, expect_response=0):
        if not self._pipe:
            return None
        try:
            payload = (line + "\n").encode("utf-8")
            written = c_ulong()
            buf = ctypes.create_string_buffer(payload)
            if not kernel32.WriteFile(self._pipe, buf, len(payload), byref(written), None):
                return None
            if expect_response:
                out = ctypes.create_string_buffer(expect_response)
                read = c_ulong()
                if kernel32.ReadFile(self._pipe, out, expect_response, byref(read), None):
                    return out.raw[: read.value]
            return True
        except Exception:
            _log_error("send")
            return None

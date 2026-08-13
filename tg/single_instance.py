import ctypes
import hashlib
from ctypes import wintypes

import getpass

MUTEX_NAME = "Local\\TranslucentMomo_" + hashlib.sha1(
    (getpass.getuser() + "@" + __import__("socket").gethostname()).encode("utf-8")
).hexdigest()[:12]
ERROR_ALREADY_EXISTS = 183

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

_handle = None


def acquire():
    global _handle
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return True
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _handle = handle
    return True
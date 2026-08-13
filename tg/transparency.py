"""Ensure Windows "Transparency effects" is on; taskbar accents need it."""
import ctypes
import winreg

PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
VALUE_NAME = "EnableTransparency"
SPI_SETCLIENTAREAANIMATION = 0x1043
HWND_BROADCAST = 0xFFFF
WM_SETTINGCHANGE = 0x001A
SMTO_ABORTIFHUNG = 0x0002


def is_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY) as key:
            return winreg.QueryValueEx(key, VALUE_NAME)[0] == 1
    except OSError:
        return True


def _apply_setting_change():
    try:
        user32 = ctypes.windll.user32
        user32.SystemParametersInfoW(SPI_SETCLIENTAREAANIMATION, 1, None, 0)
        user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, 0, SMTO_ABORTIFHUNG, 3000, None
        )
    except Exception:
        pass


def enable():
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, PERSONALIZE_KEY, 0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_DWORD, 1)
        _apply_setting_change()
        return True
    except OSError:
        return False


def ensure():
    if is_enabled():
        return True
    return enable()

# Taskbar Glass

Makes the Windows taskbar **fully transparent** - a tiny app that sits quietly in your system tray.

- **No Python needed** - grab the exe from Releases and run it
- **Fully transparent taskbar** - shows your wallpaper right through it
- **Tray icon** with a Quit option
- **Auto-recovers** - re-applies itself if Explorer rebuilds the taskbar
- **Single instance** - a second launch just reminds you it's already running

## Install (no Python)

1. Download `TaskbarGlass.exe` from the [latest release](https://github.com/manannjoshi/TaskbarGlass/releases/latest)
2. Run it. A tinted-glass icon appears in your system tray and the taskbar goes transparent.

Windows SmartScreen may warn about an unsigned exe - click "More info" then "Run anyway".

## Before you start

Windows 11 ignores taskbar tint/translucency while the system **"Transparency effects"**
setting is OFF (Settings -> Personalization -> Effects -> Transparency effects). Taskbar
Glass enables it for you automatically at startup (a per-user setting, no admin needed).

## Build from source

Requires Python 3.8+ on Windows.

```powershell
git clone https://github.com/manannjoshi/TaskbarGlass.git
cd TaskbarGlass
py -3 -m pip install -r requirements.txt
pythonw main.py          # run from source, hidden in the tray
```

Build a standalone exe:

```powershell
build.bat                # outputs dist\TaskbarGlass.exe
```"# translucentMomo" 

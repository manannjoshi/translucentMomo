@echo off
rem Builds the native TAP DLL, then a single-file windowed exe with PyInstaller.
py -3 -m pip install --quiet pyinstaller
py -3 scripts\make_icon.py

rem Build TaskbarGlassTAP.dll (injected into explorer).
powershell -NoProfile -ExecutionPolicy Bypass -File native\build_tap.ps1
if errorlevel 1 (
    echo ERROR: native DLL build failed.
    exit /b 1
)

py -3 -m PyInstaller --noconfirm --onefile --windowed --icon assets\icon.ico ^
    --name TranslucentMomo ^
    --hidden-import pystray --hidden-import pystray._win32 --collect-submodules pystray ^
    --add-binary native\TaskbarGlassTAP.dll;. ^
    main.py
if errorlevel 1 exit /b 1

rem Ship the DLL next to the exe too (preferred location at runtime).
copy /y native\TaskbarGlassTAP.dll dist\TaskbarGlassTAP.dll >nul

echo.
echo Done. dist\TranslucentMomo.exe + dist\TaskbarGlassTAP.dll

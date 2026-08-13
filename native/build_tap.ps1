# Builds TaskbarGlassTAP.dll (x64) with cl.exe from the installed VS Build Tools.
# Requires: VS Build Tools 2022 with MSVC + Windows SDK (checked at first use).

param(
    [switch]$Fast
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$OutDir = Join-Path $Root 'native'
$Src = Join-Path $OutDir 'TaskbarGlassTAP.cpp'
$Dll = Join-Path $OutDir 'TaskbarGlassTAP.dll'

$VsRoot = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools'
$MsvcDir = Join-Path $VsRoot 'VC\Tools\MSVC'
$MsvcVer = (Get-ChildItem $MsvcDir -Directory | Sort-Object Name -Descending | Select-Object -First 1).Name
$MsvcInc = Join-Path $MsvcDir "$MsvcVer\include"
$MsvcLib = Join-Path $MsvcDir "$MsvcVer\lib\x64"

$KitRoot = 'C:\Program Files (x86)\Windows Kits\10'
$KitVer = (Get-ChildItem (Join-Path $KitRoot 'Include') -Directory | Sort-Object Name -Descending | Select-Object -First 1).Name
$KitInc = Join-Path $KitRoot "Include\$KitVer"

$WinrtGen = 'C:\Users\pc\AppData\Local\Temp\opencode\cppwinrt\gen'

if (-not (Test-Path $Src)) { throw "source not found: $Src" }
if (-not (Test-Path $MsvcInc)) { throw "MSVC include not found: $MsvcInc" }
if (-not (Test-Path (Join-Path $KitInc 'um'))) { throw "SDK include not found" }

$env:INCLUDE = "$MsvcInc;$KitInc\um;$KitInc\shared;$KitInc\ucrt;$KitInc\winrt;$WinrtGen"
$env:LIB = "$MsvcLib;$KitRoot\Lib\$KitVer\um\x64;$KitRoot\Lib\$KitVer\ucrt\x64"
$env:PATH = (Join-Path $MsvcDir "$MsvcVer\bin\Hostx64\x64") + ';' + $env:PATH

$Cl = Join-Path $MsvcDir "$MsvcVer\bin\Hostx64\x64\cl.exe"

Write-Host "MSVC : $MsvcVer"
Write-Host "SDK  : $KitVer"
Write-Host "Cl   : $Cl"

# After an injection the DLL is pinned inside explorer.exe (InitializeXamlDiagnosticsEx)
# and cannot be overwritten. When that happens, keep the existing binary.
$locked = $false
if (Test-Path $Dll) {
    try {
        $fs = [System.IO.File]::Open($Dll, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        $fs.Dispose()
    }
    catch {
        $locked = $true
    }
}

if ($locked) {
    Write-Host ""
    Write-Host "TaskbarGlassTAP.dll is loaded in explorer.exe (pinned) - keeping existing binary."
    Write-Host "Restart explorer.exe if you need to rebuild the DLL."
    exit 0
}

$args_ = @(
    '/nologo', '/std:c++20', '/EHsc', '/permissive-', '/W3',
    '/DUNICODE', '/D_UNICODE', '/D_WIN32_WINNT=0x0A00',
    '/LD',
    $Src
)
if ($Fast -ne $true) { $args_ += @('/O2') }

Set-Location -LiteralPath $OutDir
& $Cl @args_ '/link' '/DLL' '/DEF:TaskbarGlassTAP.def' "/OUT:$Dll" `
    'kernel32.lib' 'user32.lib' 'ole32.lib' 'oleaut32.lib' 'advapi32.lib' 'runtimeobject.lib' 'windowsapp.lib' 'dxgi.lib'

if ($LASTEXITCODE -ne 0) { throw "cl failed with exit code $LASTEXITCODE" }

if (Test-Path $Dll) {
    Write-Host ""
    Write-Host "Built: $Dll"
    Get-Item $Dll | Select-Object FullName, Length, LastWriteTime
}
# Gera o executável (PyInstaller) e o instalador (Inno Setup).
#   .\build.ps1 -Version 1.2.3
#   .\build.ps1 -Version 1.2.3 -SkipInstaller
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Version = $Version.TrimStart("v", "V")

$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

Write-Host "==> Configuração embutida (versão $Version)"
& $python tools\write_build_config.py --version $Version
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> PyInstaller"
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
& $python -m PyInstaller main.py `
    --noconfirm --clean --windowed `
    --name SharedScreen `
    --icon assets\icon.ico `
    --add-data "assets;assets" `
    --collect-all aiortc `
    --collect-all aioice `
    --collect-all av `
    --collect-submodules realtime `
    --collect-binaries pyaudiowpatch `
    --collect-data sounddevice `
    --hidden-import pyaudiowpatch `
    --exclude-module tkinter `
    --exclude-module PySide6.QtWebEngineCore `
    --exclude-module PySide6.Qt3DCore `
    --exclude-module PySide6.QtQuick
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> Removendo partes do Qt que o app não usa"
$qt = "dist\SharedScreen\_internal\PySide6"
@(
    "opengl32sw.dll", "Qt6Quick.dll", "Qt6Qml.dll", "Qt6QmlModels.dll", "Qt6QmlMeta.dll",
    "Qt6QmlWorkerScript.dll", "Qt6Pdf.dll", "Qt6VirtualKeyboard.dll", "translations",
    "plugins\imageformats\qpdf.dll", "plugins\platforminputcontexts"
) | ForEach-Object { Remove-Item -Recurse -Force (Join-Path $qt $_) -ErrorAction SilentlyContinue }

if ($SkipInstaller) { exit 0 }

Write-Host "==> Inno Setup"
$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    $iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) { throw "Inno Setup 6 não encontrado. Instale com: winget install JRSoftware.InnoSetup" }

& $iscc "/DAppVersion=$Version" installer.iss
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "==> Pronto: dist-installer\SharedScreen-Setup-$Version.exe"

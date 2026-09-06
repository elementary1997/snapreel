<#
.SYNOPSIS
    Установка snapreel на Windows с нуля.
.EXAMPLE
    .\scripts\install.ps1
    .\scripts\install.ps1 -Hotkey 'Ctrl+Alt+5'
    .\scripts\install.ps1 -Yes
#>
[CmdletBinding()]
param(
    [string]$Hotkey,
    [string]$HotkeyGif,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$venv = if ($env:SNAPREEL_VENV) { $env:SNAPREEL_VENV } else { Join-Path $root '.venv' }

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    throw 'нужен Python 3.11+ в PATH — поставьте: winget install Python.Python.3.12'
}

$versionOk = & python -c 'import sys; print(1 if sys.version_info >= (3, 11) else 0)'
if ($versionOk -ne '1') {
    throw "нужен Python 3.11 или новее, найден: $(& python --version)"
}

Write-Host "== окружение: $venv"
& python -m venv $venv
$pip = Join-Path $venv 'Scripts\pip.exe'
$app = Join-Path $venv 'Scripts\snapreel.exe'
& $pip install --quiet --upgrade pip
& $pip install --quiet -e "$root[tray]"

$setupArgs = @('setup')
if ($Hotkey)    { $setupArgs += @('--hotkey', $Hotkey) }
if ($HotkeyGif) { $setupArgs += @('--hotkey-gif', $HotkeyGif) }
if ($Yes)       { $setupArgs += '--yes' }

Write-Host '== настройка'
& $app @setupArgs

Write-Host ''
Write-Host '== проверка'
& $app doctor

Write-Host ''
Write-Host 'Готово. Хоткей уже назначен на ярлык в меню «Пуск».'
Write-Host "Поменять позже: $app hotkey set 'Ctrl+Alt+5'"

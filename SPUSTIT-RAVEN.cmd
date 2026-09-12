@echo off
setlocal
set "RAVEN_START_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $base=$env:RAVEN_START_DIR; $script=Join-Path $base 'spustit-raven.ps1'; if(-not (Test-Path -LiteralPath $script -PathType Leaf)){ $candidates=@(Get-ChildItem -LiteralPath $base -Directory | Where-Object { (Test-Path -LiteralPath (Join-Path $_.FullName 'raven_control.py') -PathType Leaf) -and (Test-Path -LiteralPath (Join-Path $_.FullName 'spustit-raven.ps1') -PathType Leaf) }); if($candidates.Count -ne 1){throw 'Vedle spoustece musi byt prave jedna kompletni slozka Raven.'}; $script=Join-Path $candidates[0].FullName 'spustit-raven.ps1' }; & $script"
if errorlevel 1 pause
exit /b

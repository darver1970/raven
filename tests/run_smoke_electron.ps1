[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)][int]$DebugPort = 9223
)

$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $root 'src\.venv\Scripts\python.exe'
$launcher = Join-Path $root 'spustit-raven.ps1'
$stopper = Join-Path $root 'stop-raven.ps1'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Testovací Python nebyl nalezen: $python" }
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) { throw "Launcher nebyl nalezen: $launcher" }

$profile = Join-Path $root ("runtime\electron-smoke-{0}" -f ([Guid]::NewGuid().ToString('N')))
$previousDev = $env:RAVEN_DESKTOP_DEV
$previousPort = $env:RAVEN_DEBUG_PORT
$previousProfile = $env:RAVEN_ELECTRON_PROFILE
$settingsFile = Join-Path $root 'runtime\raven-settings.json'
$originalSettings = if (Test-Path -LiteralPath $settingsFile) { [IO.File]::ReadAllBytes($settingsFile) } else { $null }
$projectsFile = Join-Path $root 'runtime\raven-projects.json'
$originalProjects = if (Test-Path -LiteralPath $projectsFile) { [IO.File]::ReadAllBytes($projectsFile) } else { $null }
try {
    & $stopper -InstallRoot $root -TimeoutSeconds 15
    $env:RAVEN_DESKTOP_DEV = '1'
    $env:RAVEN_DEBUG_PORT = [string]$DebugPort
    $env:RAVEN_ELECTRON_PROFILE = $profile
    & $launcher
    $deadline = [DateTime]::UtcNow.AddSeconds(120)
    $readyCount = 0
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri ("http://127.0.0.1:{0}/json/version" -f $DebugPort) -TimeoutSec 2
            if ($response.StatusCode -eq 200 -and $response.Content -match 'webSocketDebuggerUrl') { $readyCount++ } else { $readyCount = 0 }
        } catch { $readyCount = 0 }
        if ($readyCount -ge 2) { break }
        Start-Sleep -Milliseconds 400
    } while ([DateTime]::UtcNow -lt $deadline)
    if ($readyCount -lt 2) { throw "Electron neotevřel stabilní diagnostický port $DebugPort." }
    & $python (Join-Path $PSScriptRoot 'smoke_electron.py')
    if ($LASTEXITCODE -ne 0) { throw "Electron smoke test skončil kódem $LASTEXITCODE." }
} finally {
    $env:RAVEN_DESKTOP_DEV = $previousDev
    $env:RAVEN_DEBUG_PORT = $previousPort
    $env:RAVEN_ELECTRON_PROFILE = $previousProfile
    try { & $stopper -InstallRoot $root -TimeoutSeconds 20 } catch { Write-Warning $_.Exception.Message }
    if ($null -ne $originalSettings) { [IO.File]::WriteAllBytes($settingsFile, $originalSettings) }
    if ($null -ne $originalProjects) { [IO.File]::WriteAllBytes($projectsFile, $originalProjects) }
    elseif (Test-Path -LiteralPath $projectsFile) { Remove-Item -LiteralPath $projectsFile -Force }
    if ($profile.StartsWith((Join-Path $root 'runtime\electron-smoke-')) -and (Test-Path -LiteralPath $profile)) {
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            try {
                Remove-Item -LiteralPath $profile -Recurse -Force -ErrorAction Stop
                break
            } catch {
                if ($attempt -eq 39) { Write-Warning "Dočasný profil zůstává zamčený: $profile" }
                Start-Sleep -Milliseconds 250
            }
        }
    }
}

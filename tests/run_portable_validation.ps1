[CmdletBinding()]
param([Parameter(Mandatory)][string]$PortableRoot, [switch]$SkipModel, [switch]$RestrictedPath)
$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($PortableRoot).TrimEnd('\')
if (-not (Test-Path -LiteralPath $root -PathType Container)) { throw 'Raven portable root does not exist' }
if (-not (Test-Path -LiteralPath (Join-Path $root 'raven_control.py'))) { throw 'Not a Raven installation' }
if (-not (Test-Path -LiteralPath (Join-Path $root 'computer_control.py'))) { throw 'Portable computer control module is missing' }
$portablePython = Join-Path $root 'src\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $portablePython -PathType Leaf)) { throw 'Portable Python environment is missing' }
& $portablePython -c "from computer_control import COMPUTER; s=COMPUTER.status(); assert s['available'] and s['screenshot_available'] and s['uia_available']"
if ($LASTEXITCODE -ne 0) { throw 'Portable screen, input or UI Automation dependencies are missing' }
& $portablePython -c "import hardware_monitor as h; d=h.snapshot(); assert d['online']; assert d['system_usage'].get('cpu_percent') is not None; assert d['system_usage'].get('memory_percent') is not None; assert d['disks']; assert d['processes']"
if ($LASTEXITCODE -ne 0) { throw 'Portable native telemetry fallback is not functional' }
$settingsPath = Join-Path $root 'runtime\raven-settings.json'
$savedSettings = if(Test-Path -LiteralPath $settingsPath){[IO.File]::ReadAllBytes($settingsPath)}else{$null}
$testProfile = Join-Path $root ('runtime\portable-qa-' + [Guid]::NewGuid().ToString('N'))
$oldProfile=$env:RAVEN_ELECTRON_PROFILE
$oldPort=$env:RAVEN_DEBUG_PORT
$oldPath=$env:PATH
try {
    $env:RAVEN_ELECTRON_PROFILE=$testProfile
    $env:RAVEN_DEBUG_PORT='9223'
    if($RestrictedPath){$env:PATH="$env:SystemRoot\System32;$env:SystemRoot"}
    & (Join-Path $root 'spustit-raven.ps1')
    $deadline=[DateTime]::UtcNow.AddSeconds(600)
    do {
        $listener=Get-NetTCPConnection -LocalPort 9223 -State Listen -ErrorAction SilentlyContinue
        if($listener){break}
        Start-Sleep -Milliseconds 500
    } while([DateTime]::UtcNow -lt $deadline)
    if(-not $listener){throw 'Portable Electron did not start'}
    $projectRoot=Split-Path -Parent $PSScriptRoot
    $extraArguments=@()
    if($SkipModel){$extraArguments+='--skip-model'}
    & (Join-Path $projectRoot 'src\.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'smoke_installed_electron.py') --root $root --cdp-port 9223 --screenshot (Join-Path $projectRoot 'runtime\test-results\portable-cortex.png') @extraArguments
    if($LASTEXITCODE -ne 0){throw 'Portable end-to-end validation failed'}
} finally {
    try { & (Join-Path $root 'stop-raven.ps1') -InstallRoot $root -TimeoutSeconds 30 } catch { Write-Warning $_.Exception.Message }
    if($null -ne $savedSettings){[IO.File]::WriteAllBytes($settingsPath,$savedSettings)}
    $env:RAVEN_ELECTRON_PROFILE=$oldProfile
    $env:RAVEN_DEBUG_PORT=$oldPort
    $env:PATH=$oldPath
}

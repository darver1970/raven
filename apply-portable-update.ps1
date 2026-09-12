[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Root,
    [Parameter(Mandatory)][int]$WaitForProcessId,
    [string]$PendingState = '',
    [switch]$Rollback,
    [switch]$Relaunch
)

$ErrorActionPreference = 'Stop'
$rootPath = (Resolve-Path -LiteralPath $Root).Path
if (-not (Test-Path -LiteralPath (Join-Path $rootPath 'raven_updater.py') -PathType Leaf)) {
    throw 'Cíl není platná portable složka Raven.'
}
$runtime = Join-Path $rootPath 'runtime'
$logDirectory = Join-Path $runtime 'updates'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$log = Join-Path $logDirectory 'apply.log'

function Start-VerifiedPortableDesktop {
    param([Parameter(Mandatory)][string]$ApplicationRoot)

    $launcher = Join-Path $ApplicationRoot 'Raven Portable.exe'
    if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
        throw 'Ověřitelný EXE spouštěč Raven po aktualizaci chybí.'
    }
    # The launcher reports success only after backend health and a visible
    # desktop window have both been verified. Merely spawning it is not success.
    $started = Start-Process -FilePath $launcher -WorkingDirectory $ApplicationRoot -WindowStyle Hidden -PassThru
    if (-not $started.WaitForExit(720000)) {
        throw 'Spuštění aktualizovaného Raven nebylo potvrzeno do 12 minut. Automatický rollback za běhu není bezpečný.'
    }
    $started.Refresh()
    if ($started.ExitCode -ne 0) {
        throw "Aktualizovaný Raven nepotvrdil funkční desktop (exit code $($started.ExitCode)). Záloha zůstává zachována."
    }
}

try {
    $process = Get-Process -Id $WaitForProcessId -ErrorAction SilentlyContinue
    if ($process -and -not $process.WaitForExit(120000)) {
        throw 'Raven se neukončil v časovém limitu. Aktualizace nebude aplikována za běhu.'
    }

    $python = Get-ChildItem -LiteralPath (Join-Path $runtime 'python') -Recurse -Filter 'python.exe' -File -ErrorAction SilentlyContinue |
        Sort-Object FullName | Select-Object -First 1
    if (-not $python) { throw 'Portable Python nebyl nalezen.' }
    $arguments = @((Join-Path $rootPath 'raven_updater.py'), $(if ($Rollback) { 'rollback' } else { 'apply' }), '--root', $rootPath)
    if ($PendingState -and -not $Rollback) { $arguments += @('--state', $PendingState) }
    $output = & $python.FullName @arguments 2>&1
    $output | Add-Content -LiteralPath $log -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw "Aktualizace skončila kódem $LASTEXITCODE." }

    if ($Relaunch) {
        Start-VerifiedPortableDesktop -ApplicationRoot $rootPath
        "$(Get-Date -Format o) relaunch verified by launcher" | Add-Content -LiteralPath $log -Encoding UTF8
    }
} catch {
    "$(Get-Date -Format o) ERROR $($_.Exception.Message)" | Add-Content -LiteralPath $log -Encoding UTF8
    exit 1
}

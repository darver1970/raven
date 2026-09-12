# Bezpečně ukončí pouze lokální služby patřící ke konkrétní instalaci Raven.
[CmdletBinding()]
param(
    [string]$InstallRoot = '',
    [ValidateRange(1, 30)][int]$TimeoutSeconds = 10,
    [ValidateRange(0, 2147483647)][int]$ExcludeProcessId = 0
)

$ErrorActionPreference = 'Stop'
$cleanupMutex = [Threading.Mutex]::new($false, 'Local\RavenStopV1')
try {
    $cleanupLockAcquired = $cleanupMutex.WaitOne(30000)
} catch [Threading.AbandonedMutexException] {
    $cleanupLockAcquired = $true
}
if (-not $cleanupLockAcquired) {
    throw 'Jiné ukončování aplikace Raven stále probíhá.'
}
$effectiveInstallRoot = if ([string]::IsNullOrWhiteSpace($InstallRoot)) {
    $PSScriptRoot
} else {
    $InstallRoot
}
if ([string]::IsNullOrWhiteSpace($effectiveInstallRoot)) {
    throw 'Instalační složku Raven se nepodařilo určit.'
}
$resolvedRoot = [System.IO.Path]::GetFullPath($effectiveInstallRoot).TrimEnd('\')
if (-not (Test-Path -LiteralPath $resolvedRoot -PathType Container)) {
    throw "Instalační složka Raven neexistuje: $resolvedRoot"
}
if ($resolvedRoot.Length -lt 4 -or [System.IO.Path]::GetPathRoot($resolvedRoot).TrimEnd('\') -eq $resolvedRoot) {
    throw "Odmítnuta nebezpečně široká instalační cesta: $resolvedRoot"
}

$logDirectory = Join-Path $resolvedRoot 'runtime\logs'
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
$stopLog = Join-Path $logDirectory 'stop-raven.log'
$ollamaProcessMarker = Join-Path $resolvedRoot 'runtime\state\ollama-process.json'
$serviceNames = @(
    'jarvis.exe',
    'librehardwaremonitor.exe',
    'node.exe',
    'ollama.exe',
    'python.exe',
    'pythonw.exe',
    'electron.exe',
    'raven.exe',
    'raven-desktop.exe'
)
$ravenPorts = @(5174, 8000, 8126, 11434)
$excludedProcessIds = [System.Collections.Generic.HashSet[int]]::new()
[void]$excludedProcessIds.Add([int]$PID)
if ($ExcludeProcessId -gt 0) {
    $processSnapshot = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $processById = @{}
    foreach ($snapshotProcess in $processSnapshot) {
        $processById[[int]$snapshotProcess.ProcessId] = $snapshotProcess
    }
    $ancestorId = [int]$ExcludeProcessId
    for ($depth = 0; $depth -lt 32 -and $ancestorId -gt 0; $depth++) {
        [void]$excludedProcessIds.Add($ancestorId)
        $ancestor = $processById[$ancestorId]
        if (-not $ancestor) { break }
        $parentId = [int]$ancestor.ParentProcessId
        if ($parentId -le 0 -or $parentId -eq $ancestorId) { break }
        $ancestorId = $parentId
    }
}

function Test-PathInsideRoot {
    param([AllowNull()][string]$Candidate)

    if ([string]::IsNullOrWhiteSpace($Candidate)) { return $false }
    try {
        $resolvedCandidate = [System.IO.Path]::GetFullPath($Candidate)
        return $resolvedCandidate.StartsWith(
            "$resolvedRoot\",
            [System.StringComparison]::OrdinalIgnoreCase
        )
    } catch {
        return $false
    }
}

function Test-CommandLineContainsRoot {
    param([AllowNull()][string]$CommandLine)

    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }
    return $CommandLine.IndexOf($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Test-RavenServiceSignature {
    param(
        [Parameter(Mandatory)][string]$Name,
        [AllowNull()][string]$CommandLine
    )

    $normalizedName = $Name.ToLowerInvariant()
    $command = [string]$CommandLine
    switch ($normalizedName) {
        { $_ -in @('python.exe', 'pythonw.exe') } {
            return $command -match '(?i)(raven_control\.py|hardware_monitor\.py|network_monitor\.py|-m\s+http\.server\s+5174|-m\s+openjarvis\.cli\s+serve)'
        }
        'node.exe' {
            return $command -match '(?i)(runtime[\\/]openclaw|openclaw\.mjs)'
        }
        'electron.exe' {
            return $command -match '(?i)(desktop-electron|raven-project)'
        }
        default {
            return $normalizedName -in @('jarvis.exe', 'librehardwaremonitor.exe', 'ollama.exe', 'raven.exe', 'raven-desktop.exe')
        }
    }
}

function Get-OwnedOllamaProcess {
    if (-not (Test-Path -LiteralPath $ollamaProcessMarker -PathType Leaf)) { return $null }
    try {
        $marker = Get-Content -LiteralPath $ollamaProcessMarker -Raw -Encoding utf8 | ConvertFrom-Json
        $markerRoot = [System.IO.Path]::GetFullPath([string]$marker.raven_root).TrimEnd('\')
        $markerExecutable = [System.IO.Path]::GetFullPath([string]$marker.executable)
        $markerPid = [int]$marker.pid
        if (
            $markerPid -le 0 -or
            -not $markerRoot.Equals($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)
        ) {
            return $null
        }
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $markerPid" -ErrorAction SilentlyContinue
        if (-not $process -or $process.Name -ne 'ollama.exe') { return $null }
        $processExecutable = [System.IO.Path]::GetFullPath([string]$process.ExecutablePath)
        if (-not $processExecutable.Equals($markerExecutable, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $null
        }
        $markerStart = $marker.started_at_utc
        $recordedStart = if ($markerStart -is [DateTime]) {
            $markerStart.ToUniversalTime()
        } else {
            [DateTimeOffset]::Parse(
                [string]$markerStart,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::RoundtripKind
            ).UtcDateTime
        }
        # Get-Process uses the same Windows time conversion as the launcher that
        # wrote the marker. CIM CreationDate can be shifted by the local UTC
        # offset in PowerShell 7, which made a valid owned process look foreign.
        $actualStart = (Get-Process -Id $markerPid -ErrorAction Stop).StartTime.ToUniversalTime()
        if ([Math]::Abs(($actualStart - $recordedStart).TotalSeconds) -gt 10) { return $null }
        return $process
    } catch {
        return $null
    }
}

function Get-RavenServiceProcesses {
    $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
    $ownedOllama = Get-OwnedOllamaProcess
    return @($processes | Where-Object {
        -not $excludedProcessIds.Contains([int]$_.ProcessId) -and
        $serviceNames -contains $_.Name.ToLowerInvariant() -and
        (Test-RavenServiceSignature -Name $_.Name -CommandLine $_.CommandLine) -and
        (
            (Test-PathInsideRoot -Candidate $_.ExecutablePath) -or
            (Test-CommandLineContainsRoot -CommandLine $_.CommandLine) -or
            ($ownedOllama -and $_.ProcessId -eq $ownedOllama.ProcessId)
        )
    })
}

Add-Content -LiteralPath $stopLog -Value "$(Get-Date -Format o) cleanup start root=$resolvedRoot" -Encoding utf8
$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
do {
    $ownedProcesses = Get-RavenServiceProcesses
    foreach ($process in $ownedProcesses) {
        try {
            $taskKill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
            $taskKillOutput = & $taskKill '/PID' ([string]$process.ProcessId) '/T' '/F' 2>&1
            if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue)) {
                throw "taskkill skončil kódem $LASTEXITCODE`: $($taskKillOutput -join ' ')"
            }
            Add-Content -LiteralPath $stopLog -Value "$(Get-Date -Format o) stopped pid=$($process.ProcessId) name=$($process.Name)" -Encoding utf8
        } catch {
            Add-Content -LiteralPath $stopLog -Value "$(Get-Date -Format o) stop warning pid=$($process.ProcessId) message=$($_.Exception.Message)" -Encoding utf8
        }
    }
    if ($ownedProcesses.Count -gt 0) { Start-Sleep -Milliseconds 250 }
} while ($ownedProcesses.Count -gt 0 -and [DateTime]::UtcNow -lt $deadline)

$remainingProcesses = Get-RavenServiceProcesses
$remainingOwnedPorts = New-Object System.Collections.Generic.List[int]
foreach ($port in $ravenPorts) {
    $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue
        if (
            $process -and
            (
                (Test-PathInsideRoot -Candidate $process.ExecutablePath) -or
                (Test-CommandLineContainsRoot -CommandLine $process.CommandLine)
            )
        ) {
            $remainingOwnedPorts.Add($port)
        }
    }
}

if ($remainingProcesses.Count -gt 0 -or $remainingOwnedPorts.Count -gt 0) {
    $processIds = @($remainingProcesses | ForEach-Object { $_.ProcessId }) -join ', '
    $ports = @($remainingOwnedPorts | Sort-Object -Unique) -join ', '
    throw "Některé služby Raven se nepodařilo ukončit. PID: [$processIds], porty: [$ports]."
}

if (Test-Path -LiteralPath $ollamaProcessMarker -PathType Leaf) {
    Remove-Item -LiteralPath $ollamaProcessMarker -Force -ErrorAction SilentlyContinue
}

Add-Content -LiteralPath $stopLog -Value "$(Get-Date -Format o) cleanup passed" -Encoding utf8

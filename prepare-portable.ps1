[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$DestinationRoot,
    [string]$SourceRoot = $PSScriptRoot,
    [switch]$Update
)

$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath $SourceRoot).Path.TrimEnd('\')
$destination = [IO.Path]::GetFullPath($DestinationRoot).TrimEnd('\')
if ($source -eq $destination) { throw 'Zdroj a cíl portable kopie musí být různé složky.' }
if ($destination.StartsWith($source + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $source.StartsWith($destination + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Zdroj a cíl portable kopie nesmí být vnořené složky.'
}
if (-not (Test-Path -LiteralPath (Join-Path $source 'raven_control.py') -PathType Leaf)) {
    throw 'Zdroj není platná složka Raven.'
}
if (Test-Path -LiteralPath $destination) {
    $destination = (Resolve-Path -LiteralPath $destination).Path.TrimEnd('\')
    $hasContent = $null -ne (Get-ChildItem -LiteralPath $destination -Force | Select-Object -First 1)
    if ($hasContent -and -not $Update) {
        throw 'Cíl není prázdný. Pro bezpečnou aktualizaci existující portable kopie použijte -Update.'
    }
} else {
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
}

function Copy-TreeOverlay([string]$From, [string]$To, [string[]]$ExcludedDirectories = @()) {
    if (-not (Test-Path -LiteralPath $From -PathType Container)) { return }
    New-Item -ItemType Directory -Path $To -Force | Out-Null
    $arguments = @($From, $To, '/E', '/COPY:DAT', '/DCOPY:DAT', '/R:2', '/W:1', '/XJ', '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
    $arguments += @('/XF', '.env', '.env.*', '.npmrc', 'hardware-status.json', 'network-status.json', 'voice-event.json', 'RAVEN-MAIN-PERSONAL.json', 'main-personal-drive.json')
    if ($ExcludedDirectories.Count) { $arguments += '/XD'; $arguments += $ExcludedDirectories }
    & robocopy.exe @arguments | Out-Null
    if ($LASTEXITCODE -gt 7) { throw "Kopírování selhalo kódem ${LASTEXITCODE}: $From" }
}

function Copy-OllamaModels([string]$From, [string]$To) {
    if (-not (Test-Path -LiteralPath $From -PathType Container)) { return }
    # Blob name already contains its SHA-256. Re-copying an existing blob only
    # because FAT/NTFS timestamps or attributes differ wastes many gigabytes.
    # A size mismatch is treated as corruption and the blob is replaced.
    $blobSource = Join-Path $From 'blobs'
    $blobDestination = Join-Path $To 'blobs'
    if (Test-Path -LiteralPath $blobSource -PathType Container) {
        New-Item -ItemType Directory -Path $blobDestination -Force | Out-Null
        foreach ($sourceBlob in Get-ChildItem -LiteralPath $blobSource -File) {
            $destinationBlob = Join-Path $blobDestination $sourceBlob.Name
            $destinationInfo = Get-Item -LiteralPath $destinationBlob -ErrorAction SilentlyContinue
            if ($null -eq $destinationInfo -or $destinationInfo.Length -ne $sourceBlob.Length) {
                Copy-Item -LiteralPath $sourceBlob.FullName -Destination $destinationBlob -Force
            }
        }
    }
    Copy-TreeOverlay (Join-Path $From 'manifests') (Join-Path $To 'manifests')
}

# Pouze programové soubory. Databáze a volné soubory v kořeni zdroje se
# úmyslně nekopírují, protože mohou patřit konkrétnímu uživateli.
$applicationFiles = @(
    'agent_runtime.py', 'AGENTS.md', 'apply-portable-update.ps1', 'build-portable-update.ps1', 'computer_control.py', 'CORTEX-STATUS.md',
    'hardware_monitor.py', 'LICENSE', 'network_monitor.py', 'NOTICE', 'prepare-portable.ps1',
    'Raven Portable.exe', 'raven_brain.py', 'raven_builder.py', 'raven_control.py', 'raven_cortex.py',
    'raven_evals.py', 'raven_intelligence.py', 'raven_learning.py', 'raven_next.py', 'raven_updater.py',
    'README.md', 'RELEASE_NOTES.md', 'SPUSTIT-RAVEN.cmd', 'spustit-raven.ps1',
    'stop-raven.ps1', 'telemetry_extensions.py', 'VERSION'
)
foreach ($name in $applicationFiles) {
    $item = Join-Path $source $name
    if (Test-Path -LiteralPath $item -PathType Leaf) {
        Copy-Item -LiteralPath $item -Destination (Join-Path $destination $name) -Force
    }
}

$applicationDirectories = @('defaults', 'desktop', 'desktop-electron', 'hud', 'launcher', 'src')
foreach ($name in $applicationDirectories) {
    $from = Join-Path $source $name
    $to = Join-Path $destination $name
    $excluded = @(
        (Join-Path $from '__pycache__'), (Join-Path $from '.pytest_cache'),
        (Join-Path $from '.git'), (Join-Path $from '.github'),
        (Join-Path $from 'tests'), (Join-Path $from 'docs'),
        (Join-Path $from 'examples'), (Join-Path $from 'generated-installer')
    )
    # src\.venv je povinná součást plně přenosného produktu. Launcher při
    # každém startu opraví pyvenv.cfg i editable .pth podle aktuálního disku.
    Copy-TreeOverlay $from $to $excluded
}

$runtimeSource = Join-Path $source 'runtime'
$runtimeDestination = Join-Path $destination 'runtime'
New-Item -ItemType Directory -Path $runtimeDestination -Force | Out-Null

# Přenosné závislosti a modely jsou sdílené programové prostředky. Soukromá
# uživatelská část runtime (chaty, klíče, projekty, cookies, paměť, logy a
# databáze) v tomto seznamu záměrně není.
$runtimeAssetDirectories = @(
    'git', 'librehardwaremonitor', 'ms-playwright', 'node',
    'ollama', 'ollama-models', 'presentmon', 'python',
    'python-base', 'sysinternals-handle', 'uv'
)
foreach ($name in $runtimeAssetDirectories) {
    if ($name -eq 'ollama-models') {
        Copy-OllamaModels (Join-Path $runtimeSource $name) (Join-Path $runtimeDestination $name)
    } else {
        Copy-TreeOverlay (Join-Path $runtimeSource $name) (Join-Path $runtimeDestination $name)
    }
}
# HF_HOME also contains access tokens. Distribute model cache only.
Copy-TreeOverlay (Join-Path $runtimeSource 'huggingface\hub') (Join-Path $runtimeDestination 'huggingface\hub')
# OpenClaw stores sessions, credentials and user configuration alongside its
# package. Only its program dependencies may be distributed.
Copy-TreeOverlay (Join-Path $runtimeSource 'openclaw\node_modules') (Join-Path $runtimeDestination 'openclaw\node_modules')
foreach ($packageName in @('package.json', 'package-lock.json')) {
    $packageSource = Join-Path $runtimeSource "openclaw\$packageName"
    if (Test-Path -LiteralPath $packageSource -PathType Leaf) {
        $packageDestination = Join-Path $runtimeDestination 'openclaw'
        New-Item -ItemType Directory -Path $packageDestination -Force | Out-Null
        Copy-Item -LiteralPath $packageSource -Destination (Join-Path $packageDestination $packageName) -Force
    }
}
# Runtime policies/router configuration belong to the destination user.
# Missing settings are initialized by Raven's defaults, never copied from PC.
$runtimeAssetFiles = @()
foreach ($name in $runtimeAssetFiles) {
    $item = Join-Path $runtimeSource $name
    if (Test-Path -LiteralPath $item -PathType Leaf) {
        Copy-Item -LiteralPath $item -Destination (Join-Path $runtimeDestination $name) -Force
    }
}

# Konfigurace OpenJarvis je uživatelská. Čistá kopie dostane local-first šablonu
# s vlastní cestou; při aktualizaci se existující cílová konfigurace nepřepisuje.
$destinationConfig = Join-Path $destination 'config.toml'
$configTemplate = Join-Path $destination 'defaults\openjarvis-portable.toml'
if (-not (Test-Path -LiteralPath $destinationConfig -PathType Leaf) -and (Test-Path -LiteralPath $configTemplate -PathType Leaf)) {
    $escapedDestination = $destination.Replace('\', '\\')
    $configContent = (Get-Content -LiteralPath $configTemplate -Raw -Encoding utf8).Replace('__RAVEN_ROOT_ESCAPED__', $escapedDestination)
    Set-Content -LiteralPath $destinationConfig -Value $configContent -Encoding utf8
}

$result = [ordered]@{
    status = 'passed'
    mode = if ($Update) { 'update-preserve-user-data' } else { 'clean-portable' }
    source = $source
    destination = $destination
    private_data_copied = $false
    preserved_destination_runtime = [bool]$Update
    completed_at = [DateTime]::Now.ToString('o')
}
$result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtimeDestination 'portable-update-result.json') -Encoding UTF8
$result

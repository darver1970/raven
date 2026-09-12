[CmdletBinding()]
param(
    [string]$SourceRoot = $PSScriptRoot,
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'release-output')
)

$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath $SourceRoot).Path
$versionText = (Get-Content -LiteralPath (Join-Path $source 'VERSION') -Raw).Trim().TrimStart('v')
if ($versionText -notmatch '^\d+\.\d+(\.\d+)?$') { throw 'VERSION nemá podporovaný formát.' }
$version = if (($versionText -split '\.').Count -eq 2) { "$versionText.0" } else { $versionText }
$output = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $output -Force | Out-Null
$work = Join-Path $output ("stage-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null

function Copy-Overlay([string]$From, [string]$To, [string[]]$Exclude = @()) {
    if (-not (Test-Path -LiteralPath $From)) { return }
    New-Item -ItemType Directory -Path $To -Force | Out-Null
    $arguments = @($From, $To, '/E', '/COPY:DAT', '/DCOPY:DAT', '/R:2', '/W:1', '/XJ', '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
    $arguments += @('/XF', '*.db', '*.db-*', '*.sqlite', '*.sqlite3', '*.sqlite-*', '*.sqlite3-*', '*.log', '*.pyc', '*.pyo', '*.tmp', '.env', '.env.*', 'config.toml', 'hardware-status.json', 'network-status.json', 'voice-event.json', 'RAVEN-MAIN-PERSONAL.json', 'main-personal-drive.json', 'Raven.exe')
    $Exclude += @('.git', '.venv', '__pycache__', '.pytest_cache', 'node_modules')
    if ($Exclude.Count) { $arguments += '/XD'; $arguments += $Exclude }
    & robocopy.exe @arguments | Out-Null
    if ($LASTEXITCODE -gt 7) { throw "Kopírování selhalo: $From" }
}

$files = @(
    'agent_runtime.py', 'AGENTS.md', 'apply-portable-update.ps1', 'computer_control.py',
    'CORTEX-STATUS.md', 'hardware_monitor.py', 'LICENSE', 'network_monitor.py', 'NOTICE',
    'prepare-portable.ps1', 'Raven Portable.exe', 'raven_brain.py', 'raven_builder.py', 'raven_control.py',
    'raven_cortex.py', 'raven_evals.py', 'raven_intelligence.py', 'raven_learning.py',
    'raven_next.py', 'raven_updater.py', 'README.md', 'RELEASE_NOTES.md',
    'SPUSTIT-RAVEN.cmd', 'spustit-raven.ps1', 'stop-raven.ps1',
    'telemetry_extensions.py', 'VERSION'
)
foreach ($name in $files) {
    $item = Join-Path $source $name
    if (Test-Path -LiteralPath $item -PathType Leaf) { Copy-Item -LiteralPath $item -Destination (Join-Path $work $name) -Force }
}

foreach ($name in @('defaults', 'desktop', 'hud', 'launcher', 'src')) {
    $from = Join-Path $source $name
    Copy-Overlay $from (Join-Path $work $name) @(
        (Join-Path $from '.git'), (Join-Path $from '.github'), (Join-Path $from '__pycache__'),
        (Join-Path $from '.pytest_cache'), (Join-Path $from '.venv'), (Join-Path $from 'node_modules'),
        (Join-Path $from 'tests'), (Join-Path $from 'docs'), (Join-Path $from 'examples')
    )
}

$archiveName = "Raven-Portable-Update-v$version.zip"
$archive = Join-Path $output $archiveName
if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
Compress-Archive -LiteralPath (Get-ChildItem -LiteralPath $work -Force | Select-Object -ExpandProperty FullName) -DestinationPath $archive -CompressionLevel Optimal

$manifestFiles = [ordered]@{}
Get-ChildItem -LiteralPath $work -Recurse -File | Sort-Object FullName | ForEach-Object {
    $relative = [IO.Path]::GetRelativePath($work, $_.FullName).Replace('\', '/')
    $manifestFiles[$relative] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
$archiveInfo = Get-Item -LiteralPath $archive
$manifest = [ordered]@{
    schema = 1
    version = $version
    archive = $archiveName
    size = $archiveInfo.Length
    sha256 = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    files = $manifestFiles
    created_at = [DateTime]::Now.ToString('o')
    preserves = @('runtime', 'config.toml', 'uživatelské chaty, klíče, projekty, paměť a modely')
}
$manifestPath = Join-Path $output 'raven-portable-update.json'
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
$resolvedWork = [IO.Path]::GetFullPath($work)
$resolvedOutput = [IO.Path]::GetFullPath($output).TrimEnd('\')
if (-not $resolvedWork.StartsWith($resolvedOutput + '\', [StringComparison]::OrdinalIgnoreCase) -or
    (Split-Path -Leaf $resolvedWork) -notmatch '^stage-[a-f0-9]{32}$') {
    throw 'Neplatný cíl úklidu stagingu; nic se nemaže.'
}
Remove-Item -LiteralPath $resolvedWork -Recurse -Force
[ordered]@{ archive = $archive; manifest = $manifestPath; files = $manifestFiles.Count; size = $archiveInfo.Length; sha256 = $manifest.sha256 }

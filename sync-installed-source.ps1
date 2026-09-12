[CmdletBinding()]
param([Parameter(Mandatory)][string]$InstallRoot)
$ErrorActionPreference = 'Stop'
$target = (Resolve-Path -LiteralPath $InstallRoot).Path
$payload = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$expected = Join-Path $target 'resources\raven-project'
if ($payload -ne $expected -or -not (Test-Path -LiteralPath (Join-Path $payload 'install.ps1') -PathType Leaf)) {
    throw 'Invalid installed Raven payload location'
}
# Only shipped application files are overlaid. Never remove runtime/src/user files.
foreach ($entry in Get-ChildItem -LiteralPath $payload -Force) {
    if ($entry.Name -in @('runtime', 'src', '.git')) { throw 'Unexpected private directory in installation payload' }
    Copy-Item -LiteralPath $entry.FullName -Destination $target -Recurse -Force
}
if (-not (Test-Path -LiteralPath (Join-Path $target 'src\.venv\Scripts\python.exe') -PathType Leaf)) {
    New-Item -ItemType File -Path (Join-Path $target '.raven-installing') -Force | Out-Null
}

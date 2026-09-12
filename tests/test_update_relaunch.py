"""Exercise updater relaunch decisions with mocked processes, never launching Raven."""
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("mode", ["success", "failed", "timeout", "missing", "spawn_error"])
def test_relaunch_requires_confirmed_launcher(mode):
    powershell = shutil.which("powershell")
    if not powershell:
        pytest.skip("Windows PowerShell required")
    source = (Path(__file__).resolve().parents[1] / "apply-portable-update.ps1").read_text(encoding="utf-8-sig")
    function = source[source.index("function Start-VerifiedPortableDesktop {"):source.index("\ntry {", source.index("function Start-VerifiedPortableDesktop {"))]
    script = r'''
$ErrorActionPreference = 'Stop'
$mode = '__MODE__'
$script:spawned = 0
$script:waited = 0
$script:refreshed = 0
function Test-Path { param($LiteralPath, $PathType); return $mode -ne 'missing' }
function Start-Process {
    param($FilePath, $WorkingDirectory, $WindowStyle, [switch]$PassThru)
    if ($FilePath -ne 'C:\Raven QA\Raven Portable.exe') { throw 'Wrong launcher path' }
    if ($WorkingDirectory -ne 'C:\Raven QA' -or $WindowStyle -ne 'Hidden' -or -not $PassThru) { throw 'Wrong process options' }
    $script:spawned++
    if ($mode -eq 'spawn_error') { throw 'Synthetic spawn failure' }
    $fake = [pscustomobject]@{ ExitCode = $(if ($mode -eq 'failed') {1} else {0}) }
    $fake | Add-Member ScriptMethod WaitForExit {
        param($milliseconds)
        if ($milliseconds -ne 720000) { throw 'Wrong deadline' }
        $script:waited++
        return $mode -ne 'timeout'
    }
    $fake | Add-Member ScriptMethod Refresh { $script:refreshed++ }
    return $fake
}
__FUNCTION__
$failure = $null
try { Start-VerifiedPortableDesktop -ApplicationRoot 'C:\Raven QA' } catch { $failure = $_ }
if ($mode -eq 'success' -and $failure) { throw $failure }
if ($mode -ne 'success' -and -not $failure) { throw 'Unverified desktop reported success' }
if ($mode -eq 'missing' -and $script:spawned -ne 0) { throw 'Missing EXE spawned a fallback' }
if ($mode -in @('success', 'failed', 'timeout') -and $script:waited -ne 1) { throw 'Did not wait for launcher' }
if ($mode -eq 'timeout' -and $script:refreshed -ne 0) { throw 'Read exit code before process completion' }
Write-Output 'RELAUNCH_DECISIONS_PASSED'
'''.replace("__MODE__", mode).replace("__FUNCTION__", function)
    result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "RELAUNCH_DECISIONS_PASSED" in result.stdout

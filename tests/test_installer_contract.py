from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_packaged_bootstrap_uses_selected_executable_directory() -> None:
    main_source = (ROOT / "desktop-electron" / "main.js").read_text(encoding="utf-8")

    assert "path.dirname(process.execPath)" in main_source
    assert "`-InstallPath ${quotePowerShell(INSTALLED_ROOT)}`" in main_source
    assert "const FIXED_ROOT" not in main_source
    assert "isNsisInstall: IS_NSIS_INSTALL" in main_source
    assert "savedRootIsValid: SAVED_ROOT_IS_VALID" in main_source


def test_nsis_path_named_desktop_is_not_misclassified_as_portable() -> None:
    resolver = ROOT / "desktop-electron" / "install-paths.js"
    script = r"""
const { resolveExecutableContext } = require(process.argv[1]);
const installed = resolveExecutableContext({
  isPackaged: true,
  execPath: String.raw`C:\\Users\\Petr\\Desktop\\Raven.exe`,
  portableExecutableDir: '',
  savedRoot: String.raw`C:\\old-raven`,
  existsSync: () => true
});
const portable = resolveExecutableContext({
  isPackaged: true,
  execPath: String.raw`C:\\projektjarvis\\desktop\\Raven-Desktop.exe`,
  portableExecutableDir: String.raw`C:\\projektjarvis\\desktop`,
  savedRoot: '',
  existsSync: () => false
});
process.stdout.write(JSON.stringify({ installed, portable }));
"""
    result = subprocess.run(
        ["node", "-e", script, str(resolver)],
        check=True,
        capture_output=True,
        text=True,
    )
    values = json.loads(result.stdout)

    assert values["installed"]["isNsisInstall"] is True
    assert values["installed"]["isPortableBuild"] is False
    assert values["installed"]["installedRoot"].lower().endswith(r"users\petr\desktop")
    assert values["portable"]["isNsisInstall"] is False
    assert values["portable"]["isPortableBuild"] is True
    assert values["portable"]["installedRoot"].lower().endswith("projektjarvis")


def test_installer_has_persistent_log_and_failure_message() -> None:
    installer_source = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")

    assert "install-latest.log" in installer_source
    assert "INSTALACE RAVEN SELHALA" in installer_source
    assert "Start-Transcript" in installer_source


def test_bootstrap_waits_for_confirmed_installer_process() -> None:
    main_source = (ROOT / "desktop-electron" / "main.js").read_text(encoding="utf-8")

    assert "child.once('spawn'" in main_source
    assert "installer_process_started" in main_source
    assert "child.once('error'" in main_source
    assert "installer_process_error" in main_source
    assert "-Verb RunAs" in main_source
    assert "installer_process_finished" in main_source
    assert "if (!bootstrapInProgress && !launcherDelegationInProgress)" in main_source


def test_electron_smoke_profile_can_be_isolated_from_user_profile() -> None:
    main_source = (ROOT / "desktop-electron" / "main.js").read_text(encoding="utf-8")
    assert "RAVEN_ELECTRON_PROFILE" in main_source
    assert "-EncodedCommand" in main_source
    assert "process.env.SystemRoot" in main_source
    assert "'-NoLaunch'" in main_source
    assert "launcher_finished code=" in main_source


def test_every_packaged_executable_delegates_to_full_launcher() -> None:
    main_source = (ROOT / "desktop-electron" / "main.js").read_text(encoding="utf-8")

    assert "launcherDelegationInProgress" in main_source
    assert "spustit-raven.ps1" in main_source
    assert "wrapper.log" in main_source
    assert "!launcherDelegationInProgress" in main_source
    assert "'-NoDesktop'" in main_source
    assert "bootstrapInProgress = false" in main_source
    delegation_start = main_source.index("&& !bootstrapInProgress")
    assert "app.isPackaged" in main_source[delegation_start - 40 : delegation_start]


def test_installer_verifies_and_launches_completed_installation() -> None:
    installer_source = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")

    marker_removal = installer_source.index("Remove-Item -LiteralPath $installMarker")
    application_launch = installer_source.index("Write-Step 'Spouštím Raven 1.0.'")
    assert marker_removal < application_launch
    assert "spustit-raven.ps1" in installer_source[application_launch:]


def test_nsis_install_reuses_packaged_electron_shell() -> None:
    installer_source = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")
    launcher_source = (ROOT / "spustit-raven.ps1").read_text(encoding="utf-8-sig")

    assert "$installedShell = Join-Path $installRoot 'Raven.exe'" in installer_source
    assert "$desktopExecutable = $installedShell" in installer_source
    assert "$shortcut.TargetPath = $desktopExecutable" in installer_source
    assert '$installedShell = "$root\\Raven.exe"' in launcher_source


def test_installer_rejects_windows_store_python_alias() -> None:
    installer_source = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")

    assert "function Ensure-CompatiblePython" in installer_source
    assert "Python.Python.3.13" in installer_source
    assert "Microsoft\\WindowsApps" in installer_source
    assert "$compatiblePython = Ensure-CompatiblePython" in installer_source


def test_installer_bootstraps_native_build_toolchain() -> None:
    installer_source = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")

    assert "function Ensure-VisualStudioBuildTools" in installer_source
    assert "Microsoft.VisualStudio.2022.BuildTools" in installer_source
    assert "Microsoft.VisualStudio.Workload.VCTools" in installer_source
    assert "Get-Command link.exe" in installer_source
    assert "function Get-WingetCommand" in installer_source
    assert "Microsoft\\WindowsApps\\winget.exe" in installer_source


def test_installer_bootstraps_visual_cpp_runtime_and_imports_native_runtime() -> None:
    installer_source = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")

    assert "function Ensure-VisualCppRuntime" in installer_source
    assert "Microsoft.VCRedist.2015+.x64" in installer_source
    assert '"onnxruntime"' in installer_source
    assert '"torch"' not in installer_source[installer_source.index("for module in (") :]


def test_installer_starts_isolated_ollama_before_pulling_models() -> None:
    installer_source = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")

    server_start = installer_source.index("$ollamaServer = Start-Process")
    health_check = installer_source.index("/api/version", server_start)
    model_pull = installer_source.index("& $ollamaPath pull $model", health_check)
    assert server_start < health_check < model_pull
    assert '$env:OLLAMA_HOST = "127.0.0.1:$ollamaInstallPort"' in installer_source
    assert "Stop-Process -Id $ollamaServer.Id" in installer_source
    assert "Find-OllamaExecutable" in installer_source


def test_bootstrap_propagates_noninteractive_test_mode() -> None:
    main_source = (ROOT / "desktop-electron" / "main.js").read_text(encoding="utf-8")

    assert "RAVEN_INSTALL_NONINTERACTIVE" in main_source


def test_installer_downloads_have_bounded_timeouts() -> None:
    installer_source = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")

    assert "OpenJarvis-$openJarvisCommit.zip" in installer_source
    assert "-TimeoutSec 600" in installer_source
    assert installer_source.count("-TimeoutSec 90") >= 2


def test_installer_recovers_partial_openjarvis_environment() -> None:
    installer_source = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")

    assert "$openJarvisCommit" in installer_source
    assert "$openJarvisArchiveHash" in installer_source
    assert "Get-FileHash -Algorithm SHA256" in installer_source
    assert "jarvis.exe" in installer_source
    assert "Invoke-NativeChecked -FilePath $uv" in installer_source
    assert "'ensurepip', '--upgrade'" in installer_source
    assert "Push-Location -LiteralPath $electronProject" in installer_source
    assert ".raven-uv-sync-complete" in installer_source
    assert "$openJarvisEnvironmentReady" in installer_source


def test_installer_runs_final_health_check_before_completion() -> None:
    installer_source = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")

    verification = installer_source.index("Provádím závěrečnou kontrolu")
    marker_removal = installer_source.index("Remove-Item -LiteralPath $installMarker")
    assert verification < marker_removal
    assert "install-verification.json" in installer_source
    assert "py_compile.compile" in installer_source
    assert "--check" in installer_source


def test_installer_copies_and_validates_brain_module() -> None:
    installer_source = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")

    assert "'raven_brain.py'" in installer_source
    assert "(Join-Path $installRoot 'raven_brain.py')" in installer_source
    assert '"raven_brain.py"' in installer_source


def test_installer_preserves_codex_project_rules() -> None:
    installer_source = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")
    package = json.loads((ROOT / "desktop-electron" / "package.json").read_text(encoding="utf-8"))

    assert "'AGENTS.md'" in installer_source
    filters = package["build"]["extraResources"][0]["filter"]
    assert "AGENTS.md" in filters


def test_desktop_shutdown_runs_project_scoped_cleanup() -> None:
    main_source = (ROOT / "desktop-electron" / "main.js").read_text(encoding="utf-8")
    installer_source = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")
    launcher_source = (ROOT / "spustit-raven.ps1").read_text(encoding="utf-8-sig")
    stop_source = (ROOT / "stop-raven.ps1").read_text(encoding="utf-8-sig")

    assert "app.on('before-quit', stopRavenServices)" in main_source
    assert "stop-raven.ps1" in main_source
    assert "'stop-raven.ps1'" in installer_source
    assert "Test-PathInsideRoot" in stop_source
    assert "Test-RavenServiceSignature" in stop_source
    assert "raven_control\\.py" in stop_source
    assert "openclaw\\.mjs" in stop_source
    assert "$serviceNames" in stop_source
    assert "ollama-process.json" in stop_source
    assert "taskkill.exe" in stop_source
    assert "ParentProcessId" in stop_source
    assert "excludedProcessIds" in stop_source
    assert "Local\\RavenStopV1" in stop_source
    assert "$cleanupMutex.WaitOne(30000)" in stop_source
    assert "Get-Process -Id $markerPid" in stop_source
    assert "Get-Process -Id $markerPid" in launcher_source
    assert "[DateTimeOffset]::Parse" in stop_source


def test_launcher_does_not_stop_foreign_ollama() -> None:
    launcher_source = (ROOT / "spustit-raven.ps1").read_text(encoding="utf-8-sig")

    assert "$foreignOllamaApps" not in launcher_source
    assert "Port $Port používá jiný proces" in launcher_source
    assert "launcher.log" in launcher_source
    assert "Wait-RavenHttp" in launcher_source
    assert "/v1/agents/health" in launcher_source
    assert "ollama-process.json" in launcher_source
    assert "Test-RavenOllamaPort" in launcher_source
    assert "started_at_utc" in launcher_source


def test_launcher_supports_install_paths_with_spaces() -> None:
    launcher_source = (ROOT / "spustit-raven.ps1").read_text(encoding="utf-8-sig")

    assert '"--directory", "$root\\hud"' not in launcher_source
    assert "-ArgumentList 'hardware_monitor.py'" in launcher_source
    assert "-ArgumentList 'raven_control.py'" in launcher_source
    assert "-ArgumentList 'network_monitor.py'" in launcher_source
    assert "$electronArguments += '.'" in launcher_source
    assert "-ArgumentList $electronArguments" in launcher_source

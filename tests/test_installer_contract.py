from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_packaged_bootstrap_uses_selected_executable_directory() -> None:
    main_source = (ROOT / "desktop-electron" / "main.js").read_text(encoding="utf-8")

    assert "path.dirname(process.execPath)" in main_source
    assert "`-InstallPath ${quotePowerShell(INSTALLED_ROOT)}`" in main_source
    assert "const FIXED_ROOT" not in main_source
    assert "const IS_NSIS_INSTALL" in main_source
    assert "SAVED_ROOT_IS_VALID" in main_source


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
    assert "-EncodedCommand" in main_source
    assert "process.env.SystemRoot" in main_source
    assert "'-NoLaunch'" in main_source
    assert "launcher_finished code=" in main_source


def test_installed_nsis_executable_delegates_to_full_launcher() -> None:
    main_source = (ROOT / "desktop-electron" / "main.js").read_text(encoding="utf-8")

    assert "launcherDelegationInProgress" in main_source
    assert "spustit-raven.ps1" in main_source
    assert "wrapper.log" in main_source
    assert "!launcherDelegationInProgress" in main_source


def test_installer_verifies_and_launches_completed_installation() -> None:
    installer_source = (ROOT / "install.ps1").read_text(encoding="utf-8-sig")

    marker_removal = installer_source.index("Remove-Item -LiteralPath $installMarker")
    application_launch = installer_source.index("Write-Step 'Spouštím Raven 1.0.'")
    assert marker_removal < application_launch
    assert "spustit-raven.ps1" in installer_source[application_launch:]


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


def test_launcher_does_not_stop_foreign_ollama() -> None:
    launcher_source = (ROOT / "spustit-raven.ps1").read_text(encoding="utf-8-sig")

    assert "$foreignOllamaApps" not in launcher_source
    assert "Port $Port používá jiný proces" in launcher_source
    assert "launcher.log" in launcher_source
    assert "Wait-RavenHttp" in launcher_source
    assert "/v1/agents/health" in launcher_source


def test_launcher_supports_install_paths_with_spaces() -> None:
    launcher_source = (ROOT / "spustit-raven.ps1").read_text(encoding="utf-8-sig")

    assert '"--directory", "$root\\hud"' not in launcher_source
    assert "-ArgumentList 'hardware_monitor.py'" in launcher_source
    assert "-ArgumentList 'raven_control.py'" in launcher_source
    assert "-ArgumentList 'network_monitor.py'" in launcher_source
    assert "-ArgumentList '.'" in launcher_source

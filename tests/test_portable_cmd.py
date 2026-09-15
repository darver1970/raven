import os
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher")
@pytest.mark.parametrize("nested", [False, True])
def test_launcher_finds_renamed_root(tmp_path, nested):
    launch = tmp_path / "Start Raven.cmd"
    shutil.copy2(ROOT / "SPUSTIT-RAVEN.cmd", launch)
    root = tmp_path / "nova verze %test% & Raven" if nested else tmp_path
    root.mkdir(exist_ok=True)
    (root / "raven_control.py").write_text("# fixture", encoding="utf-8")
    (root / "spustit-raven.ps1").write_text("Write-Output 'PORTABLE_ROOT_FOUND'", encoding="ascii")
    result = subprocess.run(["cmd.exe", "/d", "/c", str(launch)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "PORTABLE_ROOT_FOUND" in result.stdout


def test_portable_validation_preserves_the_requested_drive_letter() -> None:
    validation = (ROOT / "tests" / "run_portable_validation.ps1").read_text(encoding="utf-8-sig")

    assert "[IO.Path]::GetFullPath($PortableRoot)" in validation
    assert "Resolve-Path -LiteralPath $PortableRoot" not in validation
    assert "raven-cortex.sqlite3" in validation
    assert "$savedPrivateState" in validation


def test_launcher_requires_its_bundled_ollama() -> None:
    launcher = (ROOT / "spustit-raven.ps1").read_text(encoding="utf-8-sig")
    assert 'Get-Command ollama' not in launcher
    assert '$root\\runtime\\ollama\\ollama.exe' in launcher
    assert 'Portable kopie není úplná' in launcher


def test_default_pack_builds_only_portable() -> None:
    package = json.loads((ROOT / "desktop-electron" / "package.json").read_text(encoding="utf-8"))
    assert package["scripts"]["pack"] == "npm run pack:portable"
    assert package["build"]["win"]["target"] == ["portable"]


def test_gui_launcher_cannot_hang_on_hidden_script_error_popup() -> None:
    launcher = (ROOT / "spustit-raven.ps1").read_text(encoding="utf-8-sig")
    gui = (ROOT / "launcher" / "RavenPortableLauncher.cs").read_text(encoding="utf-8-sig")
    assert '[switch]$NoErrorPopup' in launcher
    assert '-NonInteractive\\b' in launcher
    assert ' -NoErrorPopup' in gui
    assert '11434/api/version\' -Seconds 120' in launcher
    assert "raven_control.py' -Seconds 120" in launcher
    assert "127.0.0.1:8126/settings' -Seconds 120" in launcher
    assert 'control-$controlLogStamp.err.log' in launcher
    assert 'control-$controlLogStamp.out.log' in launcher
    assert '$popup.Popup("$message`n`nPodrobnosti: $launcherLog", 0,' not in launcher


def test_launcher_recovers_interrupted_update_before_services() -> None:
    launcher = (ROOT / "spustit-raven.ps1").read_text(encoding="utf-8-sig")
    recovery = launcher.index("'raven_updater.py') recover --root $root")
    assert recovery < launcher.index('$ollamaPath =')
    assert '$recoveryCode -ne 0 -or (Test-Path -LiteralPath $updateJournal' in launcher


def test_gui_launcher_returns_success_only_after_verified_start():
    gui = (ROOT / "launcher" / "RavenPortableLauncher.cs").read_text(encoding="utf-8-sig")
    main = gui[gui.index('private static void Main()'):]
    assert main.index('Environment.ExitCode = 1;') < main.index('Application.Run(')
    success = gui.index('Environment.ExitCode = 0;')
    assert gui.index('if (!healthy) throw') < success
    assert gui.index('if (!desktopVisible) throw') < success
    assert gui.count('Environment.ExitCode = 0;') == 1


def test_gui_launcher_checks_the_same_shell_selected_by_script():
    gui = (ROOT / "launcher" / "RavenPortableLauncher.cs").read_text(encoding="utf-8-sig")
    root_check = gui[gui.index('private static bool IsRavenRoot('):gui.index('private static bool HasVisibleDesktopWindow(')]
    assert 'Path.Combine(path, "Raven.exe")' in root_check
    assert 'Path.Combine(path, "desktop", "Raven-Desktop.exe")' in root_check
    assert 'GetProcessesByName(Path.GetFileNameWithoutExtension(expected))' in gui
    selection = gui[gui.index('string desktopExecutable ='):gui.index('while (DateTime.UtcNow < deadline)')]
    assert selection.index('Path.Combine(root, "Raven.exe")') < selection.index('Path.Combine(root, "desktop", "Raven-Desktop.exe")')
    assert 'if (!File.Exists(desktopExecutable))' in selection


def test_script_marks_services_ready_to_prevent_double_start():
    launcher = (ROOT / "spustit-raven.ps1").read_text(encoding="utf-8-sig")
    desktop = (ROOT / "desktop-electron" / "main.js").read_text(encoding="utf-8")
    assert "$previousServicesReady = $env:RAVEN_SERVICES_READY" in launcher
    assert "$env:RAVEN_SERVICES_READY = '1'" in launcher
    assert "$env:RAVEN_SERVICES_READY = $previousServicesReady" in launcher
    assert "process.env.RAVEN_SERVICES_READY !== '1'" in desktop

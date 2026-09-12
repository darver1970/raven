from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_generated_installer_never_calls_legacy_uninstaller():
    subprocess.run(['node', 'prepare-installer.cjs'], cwd=ROOT / 'desktop-electron', check=True)
    section = (ROOT / 'desktop-electron/generated-installer/installSection.nsh').read_text()
    assert '!insertmacro uninstallOldVersion' not in section
    assert '!insertmacro installApplicationFiles' in section
    assert '!insertmacro registryAddInstallInfo' in section


def test_overlay_preserves_runtime_and_existing_user_files(tmp_path):
    import shutil
    root = tmp_path / 'Raven with spaces'
    payload = root / 'resources/raven-project'
    payload.mkdir(parents=True)
    (payload / 'install.ps1').write_text('# payload')
    (payload / 'raven_control.py').write_text('# new source')
    (payload / 'hud').mkdir()
    (payload / 'hud/index.html').write_text('new UI')
    (root / 'hud').mkdir()
    (root / 'hud/user-note.txt').write_text('keep note')
    (root / 'runtime').mkdir()
    sentinel = root / 'runtime/user-data.txt'
    sentinel.write_text('keep history')
    helper = payload / 'sync-installed-source.ps1'
    shutil.copy2(ROOT / 'sync-installed-source.ps1', helper)
    subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(helper), '-InstallRoot', str(root)], check=True)
    assert sentinel.read_text() == 'keep history'
    assert (root / 'hud/user-note.txt').read_text() == 'keep note'
    assert (root / 'hud/index.html').read_text() == 'new UI'
    assert (root / 'raven_control.py').read_text() == '# new source'
    assert (root / '.raven-installing').exists()

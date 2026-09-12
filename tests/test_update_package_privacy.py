import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows portable packager")
def test_real_packager_excludes_personal_files(tmp_path):
    source = tmp_path / "source"
    (source / "hud" / "nested" / "__pycache__").mkdir(parents=True)
    (source / "VERSION").write_text("v1.2")
    (source / "hud" / "index.html").write_text("<html><body>test</body></html>")
    for name in ("hardware-status.json", "network-status.json", "voice-event.json", ".env", "private.db", "main-personal-drive.json"):
        (source / "hud" / name).write_text("PRIVATE_SENTINEL")
    (source / "hud" / "nested" / "__pycache__" / "private.pyc").write_bytes(b"PRIVATE_SENTINEL")
    script = Path(__file__).resolve().parents[1] / "build-portable-update.ps1"
    shell = shutil.which("pwsh")
    assert shell, "PowerShell 7 is required by this build script"
    output = tmp_path / "output"
    result = subprocess.run([shell, "-NoProfile", "-File", str(script), "-SourceRoot", str(source), "-OutputDirectory", str(output)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(output / "Raven-Portable-Update-v1.2.0.zip") as archive:
        names = [name.replace("\\", "/") for name in archive.namelist() if not name.endswith("/")]
        assert set(names) == {"VERSION", "hud/index.html"}
        assert all(b"PRIVATE_SENTINEL" not in archive.read(name) for name in archive.namelist())

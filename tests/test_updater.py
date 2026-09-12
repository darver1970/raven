import hashlib
import json
import io
import zipfile
import subprocess
import sys
import shutil
import os
from pathlib import Path

import pytest

import raven_updater


def manifest_for(files: dict[str, bytes], version: str = "1.3.0") -> dict:
    return {
        "schema": 1,
        "version": version,
        "archive": f"Raven-Portable-Update-v{version}.zip",
        "size": 100,
        "sha256": "a" * 64,
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in files.items()},
    }


def downloadable_fixture(files):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        for name, data in files.items():
            bundle.writestr(name, data)
    payload = archive.getvalue()
    manifest = manifest_for(files)
    manifest.update(size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    return {"manifest": manifest, "archive_url": "https://github.com/darver1970/raven/releases/download/v1.3.0/" + manifest["archive"]}, payload


def test_download_prepares_verified_stage_and_atomic_pending(tmp_path, monkeypatch):
    update, payload = downloadable_fixture({"raven_control.py": b"new code"})
    monkeypatch.setattr(raven_updater, "_open_update_url", lambda *args: io.BytesIO(payload))
    raven_updater.download_and_stage(update, tmp_path)
    pending = json.loads((tmp_path / "runtime/updates/pending.json").read_text())
    assert (Path(pending["stage"]) / "raven_control.py").read_bytes() == b"new code"
    assert not list((tmp_path / "runtime/updates").glob("state-*.tmp"))


def test_download_refuses_parallel_update_and_pending_recovery(tmp_path, monkeypatch):
    update, payload = downloadable_fixture({"raven_control.py": b"new code"})
    calls = []
    monkeypatch.setattr(raven_updater, "_open_update_url", lambda *args: calls.append(True) or io.BytesIO(payload))
    with raven_updater.update_lock(tmp_path):
        with pytest.raises(ValueError, match="právě pracuje"):
            raven_updater.download_and_stage(update, tmp_path)
    (tmp_path / "runtime/updates/transaction.json").write_text("{}")
    with pytest.raises(ValueError, match="recovery"):
        raven_updater.download_and_stage(update, tmp_path)
    assert not calls


def test_download_stops_at_manifest_size_and_keeps_previous_pending(tmp_path, monkeypatch):
    update, payload = downloadable_fixture({"raven_control.py": b"new code"})
    updates = tmp_path / "runtime" / "updates"
    updates.mkdir(parents=True)
    pending = updates / "pending.json"
    pending.write_bytes(b'{"previous":"keep"}')
    reads = []

    class OversizedResponse(io.BytesIO):
        def read(self, size=-1):
            reads.append(size)
            if len(reads) > 1:
                pytest.fail("Oversized download continued after exceeding declared size")
            return payload + b"extra"

    monkeypatch.setattr(raven_updater, "_open_update_url", lambda *args: OversizedResponse())
    with pytest.raises(ValueError, match="velikost uvedenou"):
        raven_updater.download_and_stage(update, tmp_path)
    assert len(reads) == 1
    assert pending.read_bytes() == b'{"previous":"keep"}'
    assert not list(updates.glob("*.download"))
    assert not list(updates.glob("stage-*"))
    assert not (updates / update["manifest"]["archive"]).exists()


@pytest.mark.parametrize("name", ["../escape.py", "C:/escape.py", "/absolute.py", "runtime/raven-chats.json", "config.toml", ".git/config", "hud/hud.js:secret", "runtime./key", "hud/NUL.txt", "hud/../key", "hud//file.js", "hud/file.js "])
def test_manifest_rejects_unsafe_or_private_paths(name: str) -> None:
    with pytest.raises(ValueError):
        raven_updater.validate_manifest(manifest_for({name: b"secret"}))


def test_apply_stage_updates_program_and_preserves_private_runtime(tmp_path: Path) -> None:
    root = tmp_path / "Raven"
    stage = root / "runtime" / "updates" / "stage-v1.3.0"
    stage.mkdir(parents=True)
    (root / "raven_control.py").write_text("old", encoding="utf-8")
    (root / "VERSION").write_text("v1.2\n", encoding="utf-8")
    (root / "config.toml").write_text("friend-key", encoding="utf-8")
    private = root / "runtime" / "raven-chats.json"
    private.write_text('{"friend":true}', encoding="utf-8")
    files = {"raven_control.py": b"new", "hud/hud.js": b"updated"}
    for relative, content in files.items():
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    manifest = manifest_for(files)
    pending = root / "runtime" / "updates" / "pending.json"
    pending.write_text(json.dumps({"stage": str(stage), "manifest": manifest}), encoding="utf-8")

    result = raven_updater.apply_stage(root, pending)

    assert result["status"] == "applied"
    assert (root / "raven_control.py").read_text(encoding="utf-8") == "new"
    assert (root / "hud" / "hud.js").read_text(encoding="utf-8") == "updated"
    assert (root / "config.toml").read_text(encoding="utf-8") == "friend-key"
    assert private.read_text(encoding="utf-8") == '{"friend":true}'
    assert (Path(result["backup"]) / "raven_control.py").read_text(encoding="utf-8") == "old"
    assert not pending.exists()

    rollback = raven_updater.rollback_last(root)
    assert rollback["status"] == "rolled_back"
    assert (root / "raven_control.py").read_text(encoding="utf-8") == "old"
    assert not (root / "hud" / "hud.js").exists()
    assert (root / "VERSION").read_text(encoding="utf-8") == "v1.2\n"
    assert (root / "config.toml").read_text(encoding="utf-8") == "friend-key"


def test_apply_stage_refuses_changed_staged_file(tmp_path: Path) -> None:
    root = tmp_path / "Raven"
    stage = root / "runtime" / "updates" / "stage-v1.3.0"
    stage.mkdir(parents=True)
    (stage / "README.md").write_bytes(b"tampered")
    pending = root / "runtime" / "updates" / "pending.json"
    pending.write_text(json.dumps({"stage": str(stage), "manifest": manifest_for({"README.md": b"expected"})}), encoding="utf-8")
    with pytest.raises(ValueError, match="neprošel kontrolou"):
        raven_updater.apply_stage(root, pending)
    assert not (root / "README.md").exists()


def test_version_comparison_does_not_treat_same_release_as_update(monkeypatch) -> None:
    release = {"assets": [
        {"name": raven_updater.MANIFEST_ASSET, "browser_download_url": "https://example.test/manifest"},
        {"name": "Raven-Portable-Update-v1.2.0.zip", "browser_download_url": "https://github.com/darver1970/raven/releases/download/v1.2.0/update.zip"},
    ]}
    manifest = manifest_for({"README.md": b"ok"}, "1.2.0")
    monkeypatch.setattr(raven_updater, "_json_url", lambda url, timeout=30: release if url.endswith("latest") else manifest)
    assert raven_updater.check("1.2.0", release_api="https://api.test/latest")["status"] == "current"


def test_manifest_rejects_windows_case_collision():
    with pytest.raises(ValueError, match="kolidující"):
        raven_updater.validate_manifest(manifest_for({"hud/a.js": b"one", "HUD/A.JS": b"two"}))


@pytest.mark.parametrize("url", [
    "https://github.com/other/repo/releases/download/v1/a.zip",
    "https://github.com.evil.test/darver1970/raven/releases/download/v1/a.zip",
    "http://github.com/darver1970/raven/releases/download/v1/a.zip",
    "https://user:password@github.com/darver1970/raven/releases/download/v1/a.zip",
    "https://github.com/darver1970/raven/releases/download/v1/%2fetc",
])
def test_update_origin_is_exact_repository(url):
    with pytest.raises(ValueError):
        raven_updater.validate_origin_url(url)


def test_official_update_origins_are_accepted():
    raven_updater.validate_origin_url(raven_updater.RELEASE_API)
    raven_updater.validate_origin_url("https://github.com/darver1970/raven/releases/download/v1.3.0/Raven-Portable-Update-v1.3.0.zip")


def test_missing_backup_does_not_delete_created_files(tmp_path):
    updates = tmp_path / "runtime" / "updates"
    updates.mkdir(parents=True)
    backup = tmp_path / "runtime" / "update-backups" / "old"
    backup.mkdir(parents=True)
    created = tmp_path / "new.py"
    created.write_text("keep me")
    (updates / "last-result.json").write_text(json.dumps({
        "backup": str(backup), "created": ["new.py"], "replaced": ["missing.py"],
    }))
    with pytest.raises(ValueError, match="chybí soubor"):
        raven_updater.rollback_last(tmp_path)
    assert created.read_text() == "keep me"


def test_prepare_uses_fresh_stage_and_preserves_previous_stage(tmp_path, monkeypatch):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("README.md", b"updated")
    contents = archive.getvalue()
    manifest = manifest_for({"README.md": b"updated"})
    manifest.update(size=len(contents), sha256=hashlib.sha256(contents).hexdigest())
    monkeypatch.setattr(raven_updater, "_open_update_url", lambda *args: io.BytesIO(contents))
    old_stage = tmp_path / "runtime" / "updates" / "stage-v1.3.0"
    old_stage.mkdir(parents=True)
    (old_stage / "keep.txt").write_text("previous stage")
    result = raven_updater.download_and_stage({
        "manifest": manifest,
        "archive_url": f"https://github.com/darver1970/raven/releases/download/v1.3.0/{manifest['archive']}",
    }, tmp_path)
    assert Path(result["stage"]) != old_stage
    assert (Path(result["stage"]) / "README.md").read_bytes() == b"updated"
    assert (old_stage / "keep.txt").read_text() == "previous stage"


@pytest.mark.parametrize("recovery_mode", ["python", "launcher", "launcher_corrupt"])
def test_interrupted_apply_recovers_original_files(tmp_path, monkeypatch, recovery_mode):
    if recovery_mode.startswith("launcher") and os.name != "nt":
        pytest.skip("Windows PowerShell launcher")
    stage = tmp_path / "runtime" / "updates" / "stage-test"
    stage.mkdir(parents=True)
    files = {"raven_control.py": b"new code", "new.py": b"new file"}
    for name, content in files.items():
        (stage / name).write_bytes(content)
    (tmp_path / "raven_control.py").write_bytes(b"old code")
    (tmp_path / "VERSION").write_bytes(b"v1.2\n")
    pending = stage.parent / "pending.json"
    pending.write_text(json.dumps({"stage": str(stage), "manifest": manifest_for(files)}))
    original_replace = raven_updater.os.replace

    class SimulatedProcessCrash(BaseException):
        pass

    def crash_after_first_replace(source, target):
        original_replace(source, target)
        if Path(target) == tmp_path / "raven_control.py":
            raise SimulatedProcessCrash()

    monkeypatch.setattr(raven_updater.os, "replace", crash_after_first_replace)
    with pytest.raises(SimulatedProcessCrash):
        raven_updater.apply_stage(tmp_path)
    assert (tmp_path / "raven_control.py").read_bytes() == b"new code"
    with pytest.raises(ValueError, match="recovery"):
        raven_updater.apply_stage(tmp_path)
    monkeypatch.setattr(raven_updater.os, "replace", original_replace)
    if recovery_mode == "python":
        assert raven_updater.recover_interrupted(tmp_path)["status"] == "recovered"
    else:
        source_root = Path(__file__).resolve().parents[1]
        shutil.copy2(source_root / "raven_updater.py", tmp_path / "raven_updater.py")
        if recovery_mode == "launcher_corrupt":
            journal = json.loads((stage.parent / "transaction.json").read_text())
            (Path(journal["backup"]) / "raven_control.py").write_bytes(b"corrupt backup")
        launcher = (source_root / "spustit-raven.ps1").read_text(encoding="utf-8-sig")
        recovery_block = launcher[launcher.index("    $updateJournal ="):launcher.index('$ollamaPath =')]
        def ps_literal(value):
            return "'" + str(value).replace("'", "''") + "'"
        script = "$ErrorActionPreference='Stop'; $root=" + ps_literal(tmp_path) + "; $pythonPath=" + ps_literal(sys.executable) + "; $launcherLog=" + ps_literal(tmp_path / "launcher.log") + ";\n" + recovery_block + "\nWrite-Output 'CONTINUE_SERVICES'"
        completed = subprocess.run([shutil.which("powershell"), "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, timeout=30)
        if recovery_mode == "launcher_corrupt":
            assert completed.returncode != 0
            assert "CONTINUE_SERVICES" not in completed.stdout
            assert (stage.parent / "transaction.json").exists()
            assert (tmp_path / "raven_control.py").read_bytes() == b"new code"
            return
        assert completed.returncode == 0, completed.stderr
        assert "CONTINUE_SERVICES" in completed.stdout
    assert (tmp_path / "raven_control.py").read_bytes() == b"old code"
    assert (tmp_path / "VERSION").read_bytes() == b"v1.2\n"
    assert not (tmp_path / "new.py").exists()
    assert raven_updater.recover_interrupted(tmp_path)["status"] == "no_recovery_needed"


def test_update_lock_rejects_concurrent_writer(tmp_path):
    with raven_updater.update_lock(tmp_path):
        with pytest.raises(ValueError, match="Jiná portable aktualizace"):
            with raven_updater.update_lock(tmp_path):
                pytest.fail("Second writer acquired lock")
    with raven_updater.update_lock(tmp_path):
        pass


def test_real_process_exit_releases_lock_and_can_be_recovered(tmp_path):
    stage = tmp_path / "runtime" / "updates" / "stage-test"
    stage.mkdir(parents=True)
    (stage / "raven_control.py").write_bytes(b"new")
    (tmp_path / "raven_control.py").write_bytes(b"old")
    (stage.parent / "pending.json").write_text(json.dumps({
        "stage": str(stage), "manifest": manifest_for({"raven_control.py": b"new"}),
    }))
    script = '''
import os, sys
from pathlib import Path
import raven_updater as u
root = Path(sys.argv[1])
replace = os.replace
def interrupted(source, destination):
    replace(source, destination)
    if Path(destination) == root / "raven_control.py":
        os._exit(99)
u.os.replace = interrupted
u.apply_stage(root)
'''
    process = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                             cwd=Path(raven_updater.__file__).parent, timeout=20, capture_output=True)
    assert process.returncode == 99, process.stderr.decode(errors="replace")
    assert (tmp_path / "raven_control.py").read_bytes() == b"new"
    assert raven_updater.recover_interrupted(tmp_path)["status"] == "recovered"
    assert (tmp_path / "raven_control.py").read_bytes() == b"old"

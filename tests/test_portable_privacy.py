from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parent.parent


def powershell() -> str:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    assert executable
    return executable


def make_source(root: Path) -> None:
    (root / "raven_control.py").write_text("# app", encoding="utf-8")
    (root / "spustit-raven.ps1").write_text("# launcher", encoding="utf-8")
    (root / "hud").mkdir()
    (root / "hud" / "index.html").write_text("Raven", encoding="utf-8")
    (root / "desktop-electron" / "node_modules" / "dev-only").mkdir(parents=True)
    (root / "desktop-electron" / "node_modules" / "dev-only" / "index.js").write_text("dev", encoding="utf-8")
    (root / "desktop-electron" / "package.json").write_text('{"name":"raven"}', encoding="utf-8")
    (root / "defaults").mkdir()
    (root / "defaults" / "openjarvis-portable.toml").write_text(
        'db_path = "__RAVEN_ROOT_ESCAPED__\\\\runtime\\\\openjarvis-memory.db"',
        encoding="utf-8",
    )
    (root / "runtime" / "ollama").mkdir(parents=True)
    (root / "runtime" / "ollama" / "ollama.exe").write_bytes(b"runtime")
    (root / "runtime" / "ollama-models" / "blobs").mkdir(parents=True)
    (root / "runtime" / "ollama-models" / "blobs" / "sha256-test").write_bytes(b"model-blob")
    (root / "runtime" / "ollama-models" / "manifests").mkdir()
    (root / "runtime" / "ollama-models" / "manifests" / "latest").write_text("manifest")
    (root / "runtime" / "cloud-api-secrets.json").write_text("SECRET", encoding="utf-8")
    (root / "runtime" / "raven-chats.json").write_text("PRIVATE CHAT", encoding="utf-8")
    (root / "runtime" / "electron-profile").mkdir()
    (root / "runtime" / "electron-profile" / "Cookies").write_text("COOKIE", encoding="utf-8")
    (root / "config.toml").write_text("PRIVATE CONFIG", encoding="utf-8")
    (root / "runtime" / "openclaw" / "state").mkdir(parents=True)
    (root / "runtime" / "openclaw" / "state" / "sessions.json").write_text("PRIVATE SESSION")
    (root / "runtime" / "openclaw" / "openclaw.json").write_text("PRIVATE KEY")
    (root / "runtime" / "local-policy.json").write_text("PRIVATE POLICY")
    (root / "hud" / "hardware-status.json").write_text("PRIVATE TELEMETRY")
    (root / "runtime" / "huggingface" / "hub").mkdir(parents=True)
    (root / "runtime" / "huggingface" / "token").write_text("PRIVATE TOKEN")
    (root / "runtime" / "huggingface" / "stored_tokens").write_text("PRIVATE TOKENS")
    (root / "runtime" / "huggingface" / "hub" / "model.bin").write_bytes(b"model")


def run_portable(source: Path, destination: Path, update: bool = False) -> None:
    command = [
        powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(ROOT / "prepare-portable.ps1"), "-SourceRoot", str(source),
        "-DestinationRoot", str(destination),
    ]
    if update:
        command.append("-Update")
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)


def test_clean_portable_excludes_owner_data_but_keeps_runtime(tmp_path: Path) -> None:
    source, destination = tmp_path / "source", tmp_path / "portable"
    source.mkdir()
    make_source(source)
    run_portable(source, destination)
    assert (destination / "raven_control.py").is_file()
    assert (destination / "hud" / "index.html").is_file()
    assert (destination / "desktop-electron" / "package.json").is_file()
    assert not (destination / "desktop-electron" / "node_modules").exists()
    assert (destination / "runtime" / "ollama" / "ollama.exe").is_file()
    assert (destination / "runtime" / "ollama-models" / "blobs" / "sha256-test").read_bytes() == b"model-blob"
    assert not (destination / "runtime" / "cloud-api-secrets.json").exists()
    assert not (destination / "runtime" / "raven-chats.json").exists()
    assert not (destination / "runtime" / "electron-profile").exists()
    assert not (destination / "runtime" / "openclaw" / "state").exists()
    assert not (destination / "runtime" / "openclaw" / "openclaw.json").exists()
    assert not (destination / "runtime" / "local-policy.json").exists()
    assert not (destination / "hud" / "hardware-status.json").exists()
    assert not (destination / "runtime" / "huggingface" / "token").exists()
    assert not (destination / "runtime" / "huggingface" / "stored_tokens").exists()
    assert (destination / "runtime" / "huggingface" / "hub" / "model.bin").read_bytes() == b"model"
    config = (destination / "config.toml").read_text(encoding="utf-8-sig")
    assert "PRIVATE CONFIG" not in config
    assert str(destination).replace("\\", "\\\\") in config
    result = json.loads((destination / "runtime" / "portable-update-result.json").read_text(encoding="utf-8-sig"))
    assert result["private_data_copied"] is False


def test_portable_update_preserves_destination_user_data(tmp_path: Path) -> None:
    source, destination = tmp_path / "source", tmp_path / "portable"
    source.mkdir()
    make_source(source)
    (destination / "runtime").mkdir(parents=True)
    chats = destination / "runtime" / "raven-chats.json"
    secrets = destination / "runtime" / "cloud-api-secrets.json"
    chats.write_text("FRIEND CHAT", encoding="utf-8")
    secrets.write_text("FRIEND SECRET", encoding="utf-8")
    run_portable(source, destination, update=True)
    assert chats.read_text(encoding="utf-8") == "FRIEND CHAT"
    assert secrets.read_text(encoding="utf-8") == "FRIEND SECRET"
    assert (destination / "runtime" / "ollama" / "ollama.exe").read_bytes() == b"runtime"


def test_portable_update_repairs_truncated_model_blob(tmp_path: Path) -> None:
    source, destination = tmp_path / "source", tmp_path / "portable"
    source.mkdir()
    make_source(source)
    target = destination / "runtime" / "ollama-models" / "blobs" / "sha256-test"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"broken")
    run_portable(source, destination, update=True)
    assert target.read_bytes() == b"model-blob"


def test_portable_rejects_nested_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    make_source(source)
    destination = source / "nested"
    try:
        run_portable(source, destination)
    except subprocess.CalledProcessError:
        pass
    else:
        raise AssertionError("Nested copy must be rejected")
    assert not destination.exists()

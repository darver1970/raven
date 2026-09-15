import io
import json
from pathlib import Path

import pytest

import raven_network
import raven_next
import raven_updater


def test_network_policy_allows_loopback_and_blocks_remote_offline(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "raven-1.2-settings.json").write_text('{"offline_mode": true}', encoding="utf-8")
    settings = raven_network.load_network_settings(tmp_path)
    assert raven_network.require_network_url("http://127.0.0.1:11434/api/chat", settings) == "http://127.0.0.1:11434/api/chat"
    assert raven_network.is_loopback_url("http://[::1]:8126/health")
    with pytest.raises(ValueError, match="offline"):
        raven_network.require_network_url("https://api.github.com/", settings)


def test_broken_network_settings_fail_closed(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "raven-1.2-settings.json").write_text("{broken", encoding="utf-8")
    assert raven_network.offline_enabled(raven_network.load_network_settings(tmp_path))


def test_updater_does_not_open_url_when_offline(tmp_path: Path, monkeypatch) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "raven-1.2-settings.json").write_text('{"safe_mode": true}', encoding="utf-8")
    monkeypatch.setattr(raven_updater, "_json_url", lambda _url: pytest.fail("network was opened"))
    with pytest.raises(ValueError, match="offline"):
        raven_updater.check("1.2.1", root=tmp_path)


def test_ollama_pull_is_blocked_offline_before_process(monkeypatch) -> None:
    monkeypatch.setattr(raven_next, "load_settings", lambda: {"offline_mode": True, "safe_mode": False})
    monkeypatch.setattr(raven_next.subprocess, "run", lambda *_a, **_k: pytest.fail("process was started"))
    with pytest.raises(ValueError, match="offline"):
        raven_next.manage_ollama_model({"action": "pull", "model": "qwen3.5:4b"})


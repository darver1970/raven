from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
from datetime import datetime

import pytest
from pydantic import ValidationError

from agent_runtime import AgentRuntime, AgentTask
import raven_control
from raven_control import RAVEN_SYSTEM_PROMPT, automatic_provider_order, generate_artifact_content, normalize_provider, provider_status


@pytest.mark.parametrize("event_status,expected", [("skipped", "ready"), ("needs_verification", "paused"), ("waiting_confirmation", "paused"), ("blocked", "error")])
def test_agent_event_does_not_display_inactive_work_as_running(monkeypatch, event_status, expected):
    payload = {"agents": [{"id": "tester", "status": "working"}]}
    monkeypatch.setattr(raven_control, "load_agents", lambda: payload)
    monkeypatch.setattr(raven_control, "save_document", lambda *args: None)
    raven_control.sync_agent_event({"agent": "tester", "step": "test", "status": event_status})
    assert payload["agents"][0]["status"] == expected


def test_backend_restart_marks_old_work_as_interrupted(monkeypatch):
    payload = {"agents": [{"id": "tester", "status": "working"}, {"id": "files", "status": "ready"}]}
    monkeypatch.setattr(raven_control, "load_agents", lambda: payload)
    saved = []
    monkeypatch.setattr(raven_control, "save_document", lambda *args: saved.append(True))
    raven_control.recover_agent_activity()
    assert payload["agents"][0]["status"] == "paused"
    assert payload["agents"][1]["status"] == "ready"
    assert saved == [True]


def test_router_reports_actual_provider_before_fallback_request(monkeypatch):
    monkeypatch.setattr(raven_control, "automatic_provider_order", lambda intent: ["gemini_free", "local"])
    monkeypatch.setattr(raven_control, "record_provider_health", lambda *args: None)
    def quota(*args):
        raise raven_control.ProviderQuotaError("limit")
    monkeypatch.setattr(raven_control, "provider_request", quota)
    events = []
    def local(*args):
        assert events[-1] == ("local", "request")
        return "42"
    monkeypatch.setattr(raven_control, "local_model_request", local)
    provider, answer, fallbacks = raven_control.automatic_provider_request(
        [], "test-model", on_progress=lambda provider, phase: events.append((provider, phase)))
    assert (provider, answer) == ("local", "42")
    assert events == [("gemini_free", "request"), ("local", "request")]
    assert len(fallbacks) == 1
from raven_intelligence import detect_local_file_action, execute_file_action


ROOT = Path(__file__).resolve().parent.parent


def test_version_is_one_two() -> None:
    assert (ROOT / "VERSION").read_text(encoding="utf-8").strip() in {"1.2", "v1.2"}


def test_chat_preserves_message_identity_details_and_feedback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raven_control, "CHATS_PATH", tmp_path / "chats.json")
    saved = raven_control.save_chat({
        "id": "chat-a",
        "title": "Test",
        "messages": [
            {"id": "a" * 32, "role": "user", "content": "Dotaz"},
            {
                "id": "b" * 32, "role": "assistant", "content": "Odpověď",
                "details": "lokální model",
                "feedback": {"id": "c" * 32, "rating": 1, "approved_for_training": False},
            },
        ],
    })
    messages = saved["chats"][0]["messages"]
    assert messages[0]["id"] == "a" * 32
    assert messages[1]["details"] == "lokální model"
    assert messages[1]["feedback"] == {
        "id": "c" * 32, "rating": 1, "approved_for_training": False,
    }


def test_system_prompt_forbids_invented_provider_state() -> None:
    assert "nevkládej vlastní provozní stav" in RAVEN_SYSTEM_PROMPT
    assert "údaje zobrazuje rozhraní Ravenu samo" in RAVEN_SYSTEM_PROMPT
    assert "ověřený výsledek příslušného nástroje" in RAVEN_SYSTEM_PROMPT


def test_free_provider_catalog_has_required_order_and_no_grok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raven_control, "codex_subscription_status", lambda: {
        "available": True, "authenticated": True, "auth_method": "ChatGPT Plus", "detail": "Přihlášeno přes ChatGPT.",
    })
    payload = provider_status()
    ids = [item["id"] for item in payload["providers"]]
    assert payload["free_only"] is True
    assert payload["paid_exception"] is None
    assert payload["automatic_purchases"] is False
    order = automatic_provider_order()
    assert order[-1] == "local"
    if "gemini_free" in order and "openrouter_free" in order:
        assert order.index("gemini_free") < order.index("openrouter_free")
    assert "grok" not in " ".join(ids).lower()
    assert "xai" not in " ".join(ids).lower()
    online = [item for item in payload["providers"] if item.get("sends_data_online")]
    assert online
    assert all(item.get("privacy") and item.get("limit") for item in online)


def test_online_fallback_requires_acknowledgement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raven_control, "load_cloud_secrets", lambda: {"groq_free": "encrypted"})
    monkeypatch.setattr(raven_control, "provider_circuit_open", lambda _: False)
    monkeypatch.setattr(raven_control, "load_settings", lambda: {
        "online_provider_notice_acknowledged": False,
        "provider_order": ["groq_free", "local"],
    })
    assert raven_control.automatic_provider_order() == ["local"]


def test_v12_offline_mode_forces_local_router(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raven_control, "load_cloud_secrets", lambda: {"gemini_free": "encrypted"})
    monkeypatch.setattr(raven_control, "provider_circuit_open", lambda _: False)
    monkeypatch.setattr(raven_control, "load_settings", lambda: {
        "online_provider_notice_acknowledged": True,
        "provider_order": ["gemini_free", "local"],
    })
    monkeypatch.setattr(raven_control, "load_next_settings", lambda: {"offline_mode": True, "safe_mode": False})

    assert raven_control.automatic_provider_order() == ["local"]


def test_user_provider_order_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raven_control, "load_cloud_secrets", lambda: {"groq_free": "encrypted", "gemini_free": "encrypted"})
    monkeypatch.setattr(raven_control, "provider_circuit_open", lambda _: False)
    monkeypatch.setattr(raven_control, "load_settings", lambda: {
        "online_provider_notice_acknowledged": True,
        "provider_order": ["local", "groq_free", "gemini_free"],
    })
    assert raven_control.automatic_provider_order() == ["local", "groq_free", "gemini_free"]


def test_foreign_dpapi_key_is_not_reported_as_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = tmp_path / "cloud-api-secrets.json"
    secrets.write_text(json.dumps({"providers": {"gemini_free": "foreign-dpapi-blob"}}), encoding="utf-8")
    monkeypatch.setattr(raven_control, "CLOUD_SECRETS_PATH", secrets)
    monkeypatch.setattr(raven_control, "unprotect_secret", lambda _value: (_ for _ in ()).throw(ValueError("foreign account")))
    assert raven_control.load_cloud_secrets() == {}


def test_codex_plus_is_never_used_by_automatic_router(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raven_control, "load_cloud_secrets", lambda: {})
    monkeypatch.setattr(raven_control, "provider_circuit_open", lambda _: False)
    monkeypatch.setattr(raven_control, "codex_subscription_status", lambda: {"authenticated": True})
    assert raven_control.automatic_provider_order("coding") == ["local"]
    assert raven_control.automatic_provider_order("chat") == ["local"]


def test_scheduler_accepts_daily_and_interval_formats() -> None:
    now = datetime.fromisoformat("2026-08-28T10:00:00+02:00")
    daily = raven_control.schedule_definition("14:30", now)
    interval = raven_control.schedule_definition("každých 30 minut", now)
    assert daily["mode"] == "daily"
    assert daily["next_run"] == "2026-08-28T14:30:00+02:00"
    assert interval["mode"] == "interval"
    assert interval["cadence_seconds"] == 1800
    assert interval["next_run"] == "2026-08-28T10:30:00+02:00"


def test_scheduler_rejects_ambiguous_time() -> None:
    with pytest.raises(ValueError, match="rozpoznán"):
        raven_control.schedule_definition("někdy večer")


def test_project_identity_ignores_runtime_and_hashes_sources(tmp_path: Path) -> None:
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "secret.txt").write_text("one", encoding="utf-8")
    (tmp_path / "source.py").write_text("print('ok')", encoding="utf-8")
    first = raven_control.project_identity(tmp_path)
    (tmp_path / "runtime" / "secret.txt").write_text("two", encoding="utf-8")
    second = raven_control.project_identity(tmp_path)
    assert first["manifest_sha256"] == second["manifest_sha256"]
    assert first["source_files"] == 1


def test_integrated_terminal_executes_each_command_without_stdin_command_mode() -> None:
    source = (ROOT / "desktop-electron" / "main.js").read_text(encoding="utf-8")
    assert "RAVEN_TERMINAL_COMMAND" in source
    assert "['-NoLogo', '-NoProfile', '-NoExit', '-Command', '-']" not in source


def test_codex_plus_never_accepts_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raven_control, "codex_subscription_request", lambda _: "odpověď")
    messages = [{"role": "user", "content": "test"}]
    assert raven_control.provider_request("codex_plus", messages) == "odpověď"
    with pytest.raises(ValueError, match="nepoužívá API klíč"):
        raven_control.provider_request("codex_plus", messages, api_key="x" * 32)


def test_cloudflare_account_id_is_strictly_validated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "cloud-provider-config.json"
    monkeypatch.setattr(raven_control, "CLOUD_CONFIG_PATH", target)
    with pytest.raises(ValueError, match="32 hexadecimálních"):
        raven_control.save_cloudflare_account_id("not-valid")
    valid = "0123456789abcdef0123456789abcdef"
    assert raven_control.save_cloudflare_account_id(valid) == valid
    assert raven_control.load_cloudflare_account_id() == valid


def test_local_fallback_remains_available_when_its_previous_health_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raven_control, "load_cloud_secrets", lambda: {})
    monkeypatch.setattr(raven_control, "provider_circuit_open", lambda provider: provider == "local")
    assert raven_control.automatic_provider_order() == ["local"]


def test_automatic_router_falls_back_after_free_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(raven_control, "automatic_provider_order", lambda intent: ["gemini_free", "openrouter_free", "local"])
    monkeypatch.setattr(raven_control, "record_provider_health", lambda *_: None)

    def request(provider: str, *_: object, **__: object) -> str:
        calls.append(provider)
        if provider == "gemini_free":
            raise raven_control.ProviderQuotaError("limit vyčerpán")
        return "ověřená odpověď"

    monkeypatch.setattr(raven_control, "provider_request", request)
    selected, answer, fallbacks = raven_control.automatic_provider_request(
        [{"role": "user", "content": "test"}],
        intent="chat",
    )
    assert selected == "openrouter_free"
    assert answer == "ověřená odpověď"
    assert calls == ["gemini_free", "openrouter_free"]
    assert fallbacks == [{"provider": "gemini_free", "reason": "limit vyčerpán"}]


def test_automatic_router_retries_one_transient_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    monkeypatch.setattr(raven_control, "automatic_provider_order", lambda intent: ["gemini_free", "local"])
    monkeypatch.setattr(raven_control, "record_provider_health", lambda *_: None)
    monkeypatch.setattr(raven_control.time, "sleep", lambda *_: None)

    def request(*_: object, **__: object) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise raven_control.ProviderTransientError("dočasný výpadek")
        return "odpověď po opakování"

    monkeypatch.setattr(raven_control, "provider_request", request)
    selected, answer, fallbacks = raven_control.automatic_provider_request(
        [{"role": "user", "content": "test"}],
        intent="chat",
    )
    assert selected == "gemini_free"
    assert answer == "odpověď po opakování"
    assert fallbacks == []
    assert attempts == 2


def test_recent_events_are_scoped_to_one_chat() -> None:
    with raven_control.EVENT_LOCK:
        original = list(raven_control.EVENTS)
        raven_control.EVENTS.clear()
        raven_control.EVENTS.extend([
            {"id": "one", "chat_id": "chat-a", "task_id": "task-a", "step": "received"},
            {"id": "two", "chat_id": "chat-b", "task_id": "task-b", "step": "received"},
            {"id": "three", "chat_id": "chat-a", "task_id": "task-a", "step": "done"},
        ])
    try:
        assert [item["id"] for item in raven_control.recent_events(chat_id="chat-a")] == ["one", "three"]
        assert [item["id"] for item in raven_control.recent_events(task_id="task-b")] == ["two"]
    finally:
        with raven_control.EVENT_LOCK:
            raven_control.EVENTS.clear()
            raven_control.EVENTS.extend(original)


def test_live_event_has_versioned_identity_and_ordering(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(raven_control, "sync_agent_event", lambda _: None)
    with raven_control.EVENT_LOCK:
        original = list(raven_control.EVENTS)
    try:
        first = raven_control.emit_event("test", "working", chat_id="event-contract")
        second = raven_control.emit_event("test", "completed", chat_id="event-contract")
        assert first["schema_version"] == 1
        assert first["event_type"] == "brain.test"
        assert first["provenance"] == "live"
        assert first["session_id"] == second["session_id"]
        assert int(second["sequence"]) == int(first["sequence"]) + 1
    finally:
        with raven_control.EVENT_LOCK:
            raven_control.EVENTS.clear()
            raven_control.EVENTS.extend(original)


def test_confirmation_retry_reuses_brain_task_in_frontend() -> None:
    source = (ROOT / "hud" / "hud.js").read_text(encoding="utf-8")
    assert "brain_task_id:data.brain_task_id" in source
    assert "confirmation_token:data.confirmation_token" in source


def test_settings_link_to_supported_provider_key_pages() -> None:
    source = (ROOT / "hud" / "hud.js").read_text(encoding="utf-8")

    assert "PROVIDER_KEY_PAGES" in source
    assert "aistudio.google.com/app/apikey" in source
    assert "openrouter.ai/settings/keys" in source
    assert "console.groq.com/keys" in source
    assert "cloud.cerebras.ai/platform/" in source
    assert "console.mistral.ai/api-keys/" in source
    assert "user_models=read" in source
    assert "dash.cloudflare.com/profile/api-tokens" in source
    assert 'window.open(page.url, "_blank", "noopener,noreferrer")' in source


def test_settings_include_provider_status_codex_and_persistent_zoom() -> None:
    source = (ROOT / "hud" / "hud.js").read_text(encoding="utf-8")
    workbench = (ROOT / "hud" / "workbench.js").read_text(encoding="utf-8")
    backend = (ROOT / "raven_control.py").read_text(encoding="utf-8")

    assert "Otestovat všechny uložené klíče" in source
    assert "Přihlásit přes ChatGPT" in source
    assert "ui_zoom_percent" in source
    assert "window.setRavenZoom" in workbench
    assert '"ui_zoom_percent"' in backend


@pytest.mark.parametrize("provider", ["grok", "xai", "paid", "unknown"])
def test_forbidden_provider_is_rejected(provider: str) -> None:
    with pytest.raises(ValueError):
        normalize_provider(provider)


@pytest.mark.parametrize("model", ["grok-4", "xai/grok", "provider:paid-model"])
def test_forbidden_model_is_rejected(model: str) -> None:
    with pytest.raises(ValidationError):
        AgentTask(prompt="test", agent_id="tester", model=model)


def test_agent_runtime_never_allows_more_than_two_heavy_agents() -> None:
    assert AgentRuntime(limit=99).limit == 2


def test_agent_runtime_is_safe_across_server_threads_and_event_loops() -> None:
    runtime = AgentRuntime(limit=2)
    observed_maximum = 0
    observed_lock = threading.Lock()

    async def operation(_: AgentTask) -> int:
        nonlocal observed_maximum
        with observed_lock:
            observed_maximum = max(observed_maximum, int(runtime.status()["active"]))
        await asyncio.sleep(0.03)
        return 1

    def execute(index: int) -> int:
        task = AgentTask(prompt=f"úkol {index}", agent_id="tester", permission_mode="full")
        return asyncio.run(runtime.run(task, operation))

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(execute, range(6)))
    assert results == [1] * 6
    # Thread scheduling can serialize these short operations on a busy PC.
    # The runtime promises an upper bound, not simultaneous scheduling.
    assert 1 <= observed_maximum <= 2
    assert runtime.status()["completed"] == 6


def test_hardware_status_contains_live_dashboard_data() -> None:
    payload = json.loads((ROOT / "hud" / "hardware-status.json").read_text(encoding="utf-8"))
    assert {"system_usage", "disks", "processes", "temperatures"}.issubset(payload)
    assert all(0 <= float(disk["used"]) <= 100 for disk in payload["disks"])


def test_frontend_assets_are_local_and_present() -> None:
    html = (ROOT / "hud" / "index.html").read_text(encoding="utf-8")
    assert "https://" not in html
    for relative in ("app.css", "workbench.css", "hud.js", "workbench.js", "vendor/monaco/vs/loader.js"):
        assert (ROOT / "hud" / relative).is_file()


def test_czech_desktop_file_request_has_exact_filename() -> None:
    action = detect_local_file_action("Vytvoř soubor test.txt na ploše")
    assert action is not None
    assert Path(action["path"]).name == "test.txt"
    assert Path(action["path"]).parent.name.lower() in {"desktop", "plocha"}


def test_multiline_fenced_html_content_is_preserved(tmp_path: Path) -> None:
    target = tmp_path / "index.html"
    prompt = f'''Vytvoř soubor "{target}" s obsahem:\n```html\n<!doctype html>\n<html lang="cs"><body>Ahoj</body></html>\n```'''
    action = detect_local_file_action(prompt)
    assert action is not None
    assert Path(action["path"]) == target.resolve()
    assert action["content"].startswith("<!doctype html>")
    assert "<body>Ahoj</body>" in action["content"]


def test_html_page_request_is_detected_without_word_file(tmp_path: Path) -> None:
    target = tmp_path / "index.html"
    action = detect_local_file_action(f'Vygeneruj HTML stránku "{target}" s uvítací zprávou')
    assert action is not None
    assert action["action"] == "create_text_file"
    assert Path(action["path"]) == target.resolve()


def test_generated_artifact_strips_markdown_fence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(raven_control, "automatic_provider_request", lambda *_: ("gemini_free", "```html\n<!doctype html><html><style>body{color:white}</style><body>Ahoj</body></html>\n```", []))
    provider, content, fallbacks = generate_artifact_content("Vytvoř stránku", str(tmp_path / "index.html"))
    assert provider == "gemini_free"
    assert content.endswith("</html>")
    assert fallbacks == []


def test_generated_html_uses_local_template_for_truncated_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(raven_control, "automatic_provider_request", lambda *_: ("gemini_free", "<!doctype html><html><style>", []))
    provider, content, fallbacks = generate_artifact_content("Vytvoř stránku", str(tmp_path / "index.html"))
    assert provider == "local-template"
    assert content.endswith("</html>")
    assert "Vítejte v Raven AI" in content
    assert fallbacks[0]["provider"] == "gemini_free"


def test_generated_html_rejects_unrequested_controls_and_bad_welcome(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bad = "<!doctype html><html><style>body{color:white}</style><body><p>Vítejte v Raven AI - nesmysl.</p><button onclick='x()'>Klik</button></body></html>"
    monkeypatch.setattr(raven_control, "automatic_provider_request", lambda *_: ("gemini_free", bad, []))
    provider, content, fallbacks = generate_artifact_content("Přidej uvítací zprávu Vítejte v Raven AI", str(tmp_path / "index.html"))
    assert provider == "local-template"
    assert "<button" not in content
    assert "<p>Vítejte v Raven AI.</p>" in content
    assert fallbacks


def test_full_access_file_tool_writes_and_verifies(tmp_path: Path) -> None:
    target = tmp_path / "test.txt"
    result = execute_file_action(
        {"action": "create_text_file", "path": str(target), "content": "Raven test"},
        "full",
    )
    assert result["status"] == "completed"
    assert result["verified"] is True
    assert target.read_text(encoding="utf-8") == "Raven test"


def test_simulation_and_denied_mode_never_write(tmp_path: Path) -> None:
    target = tmp_path / "test.txt"
    action = {"action": "create_text_file", "path": str(target), "content": "nic"}
    result = execute_file_action(action, "full", simulate=True)
    assert result["status"] == "simulated"
    assert not target.exists()
    with pytest.raises(PermissionError):
        execute_file_action(action, "denied")
    assert not target.exists()


def test_existing_file_is_not_overwritten_by_create(tmp_path: Path) -> None:
    target = tmp_path / "test.txt"
    target.write_text("original", encoding="utf-8")
    with pytest.raises(FileExistsError):
        execute_file_action({"action": "create_text_file", "path": str(target), "content": "new"}, "full")
    assert target.read_text(encoding="utf-8") == "original"


def test_delete_is_recoverable_and_always_requires_confirmation(tmp_path: Path) -> None:
    target = tmp_path / "delete-me.txt"
    target.write_text("recoverable", encoding="utf-8")
    action = {"action": "delete_file", "path": str(target)}
    pending = execute_file_action(action, "full")
    assert pending["status"] == "confirmation_required"
    result = execute_file_action(action, "full", confirmed=True)
    assert result["verified"] is True
    assert not target.exists()
    recovery = Path(result["recovery_path"])
    assert recovery.read_text(encoding="utf-8") == "recoverable"
    recovery.unlink(missing_ok=True)

"""Exercise the real HTTP handler with isolated stores and deterministic inference."""
import json
import threading
import urllib.request
import urllib.error
import pytest
from http.server import ThreadingHTTPServer

import raven_control as control
from raven_brain import BrainStore, RavenBrain
from raven_cortex import CortexStore, RavenCortex
from raven_learning import LearningStore


@pytest.mark.parametrize("cortex_status", ["completed", "needs_verification"])
def test_chat_completes_with_textual_review_confidence(tmp_path, monkeypatch, cortex_status):
    monkeypatch.setattr(control, "BRAIN", RavenBrain(BrainStore(tmp_path / "brain.json")))
    monkeypatch.setattr(control, "CORTEX", RavenCortex(CortexStore(tmp_path / "cortex.db")))
    original_finalize = control.CORTEX.finalize
    if cortex_status != "completed":
        def require_verification(task_id):
            result = original_finalize(task_id)
            result["status"] = cortex_status
            control.CORTEX.store.set_status(task_id, cortex_status)
            return result
        monkeypatch.setattr(control.CORTEX, "finalize", require_verification)
    monkeypatch.setattr(control, "LEARNING", LearningStore(tmp_path / "learning.db"))
    monkeypatch.setattr(control, "load_settings", lambda: {"ai_provider": "local", "permission_mode": "confirm"})
    monkeypatch.setattr(control, "ollama_models", lambda: {"models": []})
    monkeypatch.setattr(control, "system_profile", lambda: {"ram_gb": 12})
    monkeypatch.setattr(control, "load_library_settings", lambda: {})
    monkeypatch.setattr(control, "prepare_chat_messages", lambda messages, **kw: messages)
    monkeypatch.setattr(control, "provider_request", lambda *args: "Ahoj! Jak mohu pomoci?")
    monkeypatch.setattr(control, "run_agent_stage", lambda agent, prompt, operation, **kw: operation())
    monkeypatch.setattr(control, "record_task", lambda *args: None)
    monkeypatch.setattr(control, "record_active_provider", lambda *args: None)
    events = []
    monkeypatch.setattr(control, "emit_event", lambda step, status, **details: events.append({"step": step, "status": status, **details}) or events[-1])
    server = ThreadingHTTPServer(("127.0.0.1", 0), control.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/chat",
            data=json.dumps({"messages": [{"role": "user", "content": "Ahoj"}]}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.load(response)
        assert result["answer"].startswith("Ahoj")
        assert result["review"]["confidence"] in {"low", "medium", "high"}
        assert result["cortex"]["status"] == cortex_status
        assert result["status"] == cortex_status
        assert control.BRAIN.store.get(result["brain_task_id"]).status.value == cortex_status
        assert events[-1]["status"] == cortex_status
        assert events[-1]["step"] == ("done" if cortex_status == "completed" else "needs_verification")
        task = control.CORTEX.store.task(result["cortex_task_id"])
        execute_step = next(step for step in task["steps"] if step["id"] == "execute")
        assert execute_step["attempts"] == 1
        assert execute_step["status"] == "passed"
        assert any(item["event"] == "operation_started" and item["data"]["step_id"] == "execute" for item in task["events"])
        assert not any(item["kind"] == "test" for item in task["evidence"])
        monkeypatch.setattr(control, "detect_local_file_action", lambda prompt: {"action": "write_text_file", "path": str(tmp_path / "demo.txt"), "content": "test"})
        monkeypatch.setattr(control, "execute_file_action", lambda action, mode, confirmed=False, **kw: {
            "status": "completed" if confirmed else "confirmation_required",
            "message": "Soubor byl upraven a overen" if confirmed else "Potvrdit změnu?",
            "verified": confirmed, "path": action["path"],
        })
        payload = {"messages": [{"role": "user", "content": "Uprav soubor demo.txt"}]}
        request.data = json.dumps(payload).encode()
        try:
            urllib.request.urlopen(request, timeout=10)
            assert False, "Expected confirmation"
        except urllib.error.HTTPError as error:
            assert error.code == 409
            waiting = json.load(error)
        request.data = json.dumps({**payload, "confirmed": True, "brain_task_id": waiting["brain_task_id"], "confirmation_token": waiting["confirmation_token"]}).encode()
        with urllib.request.urlopen(request, timeout=10) as response:
            resumed = json.load(response)
        assert resumed["cortex_task_id"] == waiting["cortex_task_id"]
        assert resumed["cortex"]["status"] == cortex_status
        monkeypatch.setattr(control, "ROOT", tmp_path)
        monkeypatch.setattr(control, "load_settings", lambda: {"ai_provider": "local", "permission_mode": "full"})
        monkeypatch.setattr(control, "detect_application_request", lambda prompt: {"action": "create_application", "name": "http-test-app"})
        def build_fixture(prompt, destination, generate, **kwargs):
            destination.mkdir(parents=True)
            output = destination / "index.html"
            output.write_text("<!doctype html><title>HTTP Cortex test</title>", encoding="utf-8")
            return {"name": destination.name, "root": str(destination),
                    "files": [{"path": "index.html", "characters": output.stat().st_size}],
                    "validation": {"passed": True}}
        monkeypatch.setattr(control, "build_project", build_fixture)
        request.data = json.dumps({"messages": [{"role": "user", "content": "Vytvoř aplikaci http-test-app"}]}).encode()
        with urllib.request.urlopen(request, timeout=10) as response:
            created = json.load(response)
        assert (tmp_path / "runtime" / "generated-projects" / "http-test-app" / "index.html").is_file()
        created_task = control.CORTEX.store.task(created["cortex_task_id"])
        execute_step = next(step for step in created_task["steps"] if step["id"] == "execute")
        assert execute_step["attempts"] == 1
        assert execute_step["status"] == "passed"
        assert any(event["event"] == "operation_started" for event in created_task["events"])
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)

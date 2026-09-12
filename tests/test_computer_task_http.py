"""Real HTTP boundary, without sending input to the user's desktop."""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from uuid import uuid4

import pytest

import raven_control as control


def test_unverified_input_is_not_replayed_or_automatically_reset(monkeypatch):
    calls = []
    monkeypatch.setattr(control, "require_permission", lambda *args: None)
    monkeypatch.setattr(control, "load_settings", lambda: {})
    monkeypatch.setattr(control, "emit_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(control, "run_agent_stage", lambda agent, prompt, operation, **kwargs: operation())
    monkeypatch.setattr(control, "plan_computer_task", lambda *args: {"actions": [{"type": "key", "key": "ENTER"}]})
    monkeypatch.setattr(control.COMPUTER, "execute", lambda payload: calls.append(payload) or {"success": True, "screen_changed": True})
    monkeypatch.setattr(control.COMPUTER, "verify", lambda *args: {"achieved": False, "kind": "unverified", "message": "Neověřeno"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), control.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/computer/task",
            data=json.dumps({"instruction": "Ulož dokument", "hwnd": 7, "confirmed": True, "reset_stop": True}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.load(response)
        assert result["success"] is False
        assert len(result["attempts"]) == 1
        assert len(calls) == 1
        assert "reset_stop" not in calls[0]
        cortex_task = control.CORTEX.store.task(result["cortex_task_id"])
        assert result["cortex_status"] == "blocked"
        assert next(step for step in cortex_task["steps"] if step["id"] == "execute")["status"] == "blocked"
        assert any(event["event"] == "operation_started" for event in cortex_task["events"])
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_verified_input_completes_cortex_task(monkeypatch):
    monkeypatch.setattr(control, "require_permission", lambda *args: None)
    monkeypatch.setattr(control, "load_settings", lambda: {})
    monkeypatch.setattr(control, "emit_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(control, "run_agent_stage", lambda agent, prompt, operation, **kwargs: operation())
    monkeypatch.setattr(control, "plan_computer_task", lambda *args: {"actions": [{"type": "key", "key": "ENTER"}]})
    monkeypatch.setattr(control.COMPUTER, "execute", lambda payload: {"success": True, "screen_changed": True})
    monkeypatch.setattr(control.COMPUTER, "verify", lambda *args: {
        "achieved": True, "kind": "test-postcondition", "message": "Cíl ověřen",
    })
    server = ThreadingHTTPServer(("127.0.0.1", 0), control.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/computer/task",
            data=json.dumps({"instruction": "Potvrď testovací dialog", "hwnd": 7, "confirmed": True}).encode(),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.load(response)
        assert result["success"] is True
        assert result["cortex_status"] == "completed"
        task = control.CORTEX.store.task(result["cortex_task_id"])
        assert all(step["status"] in {"passed", "skipped"} for step in task["steps"])
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


@pytest.mark.skipif(os.environ.get("RAVEN_LIVE_AI_COMPUTER_TEST") != "1", reason="explicit live local-model HTTP computer test")
def test_live_http_computer_task_is_executed_verified_and_audited() -> None:
    token = "RAVEN_HTTP_PC_OK_" + uuid4().hex[:6]
    window_marker = "Raven HTTP Computer Test " + uuid4().hex[:8]
    script = (
        "import tkinter as tk; "
        f"r=tk.Tk(); r.title({window_marker!r}); r.geometry('640x240+120+120'); "
        "e=tk.Entry(r,font=('Segoe UI',18)); e.pack(fill='x',padx=30,pady=70); e.focus_force(); "
        f"e.bind('<KeyRelease>',lambda _e:r.title({window_marker!r}+' | '+e.get())); r.mainloop()"
    )
    process = subprocess.Popen([sys._base_executable, "-c", script])
    server = ThreadingHTTPServer(("127.0.0.1", 0), control.Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 12
        window = None
        while time.monotonic() < deadline:
            window = next((item for item in control.COMPUTER.windows() if window_marker in item["title"]), None)
            if window:
                break
            time.sleep(0.15)
        assert window is not None
        control.COMPUTER.reset_stop()
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/computer/task",
            data=json.dumps({
                "instruction": f"Napiš přesně text {token} do jediného textového pole.",
                "hwnd": window["hwnd"], "confirmed": True, "model": "qwen3.5:0.8b",
            }, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            result = json.load(response)
        assert result["success"] is True, result
        assert result["verification"]["achieved"] is True
        assert result["cortex_status"] == "completed"
        assert token in next(item["title"] for item in control.COMPUTER.windows() if window_marker in item["title"])
        assert control.COMPUTER.audit_path.is_file()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

import io
import json

import raven_control


def test_long_local_history_preserves_system_rules(monkeypatch):
    captured = {}
    def reply(request, timeout):
        captured.update(json.loads(request.data))
        return io.BytesIO(b'{"message":{"content":"ok"}}')
    monkeypatch.setattr(raven_control.urllib.request, "urlopen", reply)
    history = [{"role": "system", "content": "Preserve user data"}]
    history += [{"role": "user", "content": str(index)} for index in range(30)]
    assert raven_control.local_model_request(history, "local-test") == "ok"
    assert captured["messages"][0] == history[0]
    assert captured["messages"][-1] == history[-1]
    assert len(captured["messages"]) == 16
    assert captured["keep_alive"] == "15m"
    assert captured["options"]["num_ctx"] == 4096


def test_local_model_uses_economy_budget(monkeypatch):
    captured = {}
    monkeypatch.setattr(raven_control, "load_next_settings", lambda: {
        "performance_profile": "economy", "context_budget_tokens": 12000,
    })
    def reply(request, timeout):
        captured.update(json.loads(request.data))
        return io.BytesIO(b'{"message":{"content":"ok"}}')
    monkeypatch.setattr(raven_control.urllib.request, "urlopen", reply)
    assert raven_control.local_model_request([{"role": "user", "content": "Ahoj"}], "local-test") == "ok"
    assert captured["options"]["num_ctx"] == 4096
    assert captured["keep_alive"] == "5m"

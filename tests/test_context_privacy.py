import raven_control as control


def test_private_context_is_not_read_when_sharing_disabled(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Private context must not be read")
    monkeypatch.setattr(control, "load_rules", lambda: [])
    monkeypatch.setattr(control, "load_next_settings", lambda: {"memory_enabled": True})
    monkeypatch.setattr(control, "load_project_memory", forbidden)
    monkeypatch.setattr(control, "search_project_index", forbidden)
    monkeypatch.setattr(control, "search_library", forbidden)
    monkeypatch.setattr(control, "next_memories", forbidden)
    monkeypatch.setattr(control.LEARNING, "memories", forbidden)
    result = control.prepare_chat_messages([{"role": "user", "content": "Popiš projekt Raven"}], include_library=False)
    assert result[-1]["content"] == "Popiš projekt Raven"


def test_memory_switch_disables_project_summary(monkeypatch):
    monkeypatch.setattr(control, "load_rules", lambda: [])
    monkeypatch.setattr(control, "load_next_settings", lambda: {"memory_enabled": False})
    def forbidden():
        raise AssertionError("Disabled memory was loaded")
    monkeypatch.setattr(control, "load_project_memory", forbidden)
    monkeypatch.setattr(control, "search_project_index", lambda *_: {"results": []})
    monkeypatch.setattr(control, "search_library", lambda *args, **kwargs: {"results": []})
    control.prepare_chat_messages([{"role": "user", "content": "Ahoj Raven"}])

import os
from pathlib import Path

import pytest

from computer_control import COMPUTER, ComputerControlError, ComputerController, IS_WINDOWS
import raven_control


def test_status_describes_guarded_capabilities(tmp_path: Path) -> None:
    controller = ComputerController(tmp_path)
    status = controller.status()
    assert status["available"] is IS_WINDOWS
    assert "emergency-stop" in status["capabilities"]
    assert "before-after-verification" in status["capabilities"]
    assert Path(status["audit_path"]).parent == tmp_path / "runtime" / "computer-control"


def test_protected_window_detection() -> None:
    assert ComputerController._is_protected("Windows Security", "SecurityHealthSystray.exe")
    assert ComputerController._is_protected("Přihlášení – heslo", "app.exe")
    assert not ComputerController._is_protected("Poznámkový blok", "notepad.exe")


def test_stop_requires_explicit_reset(tmp_path: Path) -> None:
    controller = ComputerController(tmp_path)
    assert controller.stop()["stopped"] is True
    assert controller.status()["emergency_stopped"] is True
    assert controller.reset_stop()["stopped"] is False


def test_invalid_sequence_is_rejected(tmp_path: Path) -> None:
    controller = ComputerController(tmp_path)
    with pytest.raises(ComputerControlError, match="1 až 100"):
        controller.execute({"hwnd": 1, "actions": []})


def test_local_planner_accepts_only_structured_computer_actions(monkeypatch) -> None:
    monkeypatch.setattr(raven_control.COMPUTER, "elements", lambda hwnd, limit: {
        "window": {"hwnd": int(hwnd), "title": "Test", "process": "test.exe", "bounds": {}},
        "backend": "uia", "count": 1,
        "elements": [{"name": "Uložit", "control_type": "Button", "automation_id": "save",
                      "class_name": "Button", "enabled": True, "visible": True, "bounds": {}}],
    })
    monkeypatch.setattr(raven_control, "local_model_request", lambda *_args: '{"actions":[{"type":"element_click","selector":{"automation_id":"save"}}],"summary":"Uložím."}')
    monkeypatch.setattr(raven_control, "load_settings", lambda: {"default_model": "qwen3.5:4b"})

    plan = raven_control.plan_computer_task("Klikni na Uložit", 123)

    assert plan["actions"][0]["type"] == "element_click"
    assert plan["accessibility_backend"] == "uia"
    assert plan["model"] == "qwen3.5:4b"


def test_local_planner_rejects_unapproved_action(monkeypatch) -> None:
    monkeypatch.setattr(raven_control.COMPUTER, "elements", lambda *_args: {
        "window": {"title": "Test", "process": "test.exe", "bounds": {}},
        "backend": "uia", "count": 0, "elements": [],
    })
    monkeypatch.setattr(raven_control, "local_model_request", lambda *_args: '{"actions":[{"type":"run_shell","command":"bad"}]}')
    with pytest.raises(ValueError, match="nepovolenou"):
        raven_control.plan_computer_task("Proveď bezpečný test", 123)


def test_local_planner_uses_exact_edit_field_for_explicit_text(monkeypatch) -> None:
    monkeypatch.setattr(raven_control.COMPUTER, "elements", lambda *_args: {
        "window": {"title": "Test", "process": "test.exe", "bounds": {}},
        "backend": "uia", "count": 2,
        "elements": [
            {"name": "", "control_type": "Edit", "automation_id": "input", "class_name": "Edit", "enabled": True, "visible": True, "bounds": {}},
            {"name": "TitleBar", "control_type": "TitleBar", "automation_id": "", "class_name": "", "enabled": True, "visible": True, "bounds": {}},
        ],
    })
    monkeypatch.setattr(
        raven_control, "local_model_request",
        lambda *_args: '{"actions":[{"type":"element_click","selector":{"name":"TitleBar"}}],"summary":"Kliknu."}',
    )
    plan = raven_control.plan_computer_task("Napiš přesně text RAVEN_OK do jediného textového pole.", 123)
    assert plan["actions"] == [{"type": "element_set_value", "selector": {"index": 0}, "text": "RAVEN_OK"}]


def test_visual_planner_accepts_only_window_relative_coordinates(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(raven_control.COMPUTER, "elements", lambda *_args: {
        "window": {"title": "Canvas", "process": "paint.exe", "bounds": {"width": 640, "height": 480}},
        "backend": "win32", "count": 0, "elements": [],
    })
    screenshot = tmp_path / "mock-image.jpg"
    screenshot.write_bytes(b"mock image for mocked model")
    monkeypatch.setattr(raven_control.COMPUTER, "capture", lambda **_kwargs: {"path": str(screenshot)})
    monkeypatch.setattr(
        raven_control, "local_model_request",
        lambda *_args: '{"actions":[{"type":"click_relative","x":320,"y":240}],"summary":"Kliknu doprostřed plátna."}',
    )
    plan = raven_control.plan_computer_task("Klikni doprostřed obrazu", 123)
    assert plan["actions"][0] == {"type": "click_relative", "x": 320, "y": 240}


def test_visual_planner_rejects_coordinate_outside_window(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(raven_control.COMPUTER, "elements", lambda *_args: {
        "window": {"title": "Canvas", "process": "paint.exe", "bounds": {"width": 640, "height": 480}},
        "backend": "win32", "count": 0, "elements": [],
    })
    screenshot = tmp_path / "mock-image.jpg"
    screenshot.write_bytes(b"mock image for mocked model")
    monkeypatch.setattr(raven_control.COMPUTER, "capture", lambda **_kwargs: {"path": str(screenshot)})
    monkeypatch.setattr(
        raven_control, "local_model_request",
        lambda *_args: '{"actions":[{"type":"click_relative","x":900,"y":240}],"summary":"Kliknu."}',
    )
    with pytest.raises(ValueError, match="mimo cílové okno"):
        raven_control.plan_computer_task("Klikni v obrazu", 123)


def test_visual_planner_refuses_coordinates_without_screenshot(monkeypatch):
    monkeypatch.setattr(raven_control.COMPUTER, "elements", lambda *_: {
        "window": {"title": "Canvas", "bounds": {"width": 640, "height": 480}},
        "backend": "win32", "count": 0, "elements": [],
    })
    monkeypatch.setattr(raven_control.COMPUTER, "capture", lambda **_: {"path": ""})
    monkeypatch.setattr(raven_control, "local_model_request", lambda *_: '{"actions":[{"type":"click_relative","x":100,"y":100}]}')
    with pytest.raises(ValueError, match="Bez snímku"):
        raven_control.plan_computer_task("Klikni na ikonu", 7)


def test_exact_text_uses_focused_field_when_toolkit_exposes_only_panes(monkeypatch) -> None:
    monkeypatch.setattr(raven_control.COMPUTER, "elements", lambda *_args: {
        "window": {"title": "Canvas UI", "process": "app.exe", "bounds": {}},
        "backend": "uia", "count": 2,
        "elements": [
            {"name": "", "control_type": "Pane", "enabled": True, "visible": True, "bounds": {}},
            {"name": "Title", "control_type": "TitleBar", "enabled": True, "visible": True, "bounds": {}},
        ],
    })
    monkeypatch.setattr(
        raven_control, "local_model_request",
        lambda *_args: '{"actions":[{"type":"element_click","selector":{"index":1}}],"summary":"Kliknu."}',
    )
    plan = raven_control.plan_computer_task("Napiš přesně text RAVEN_OK do jediného textového pole.", 7)
    assert plan["actions"] == [{"type": "type_text", "text": "RAVEN_OK"}]


def test_exact_text_verifier_requires_text_in_target_window(monkeypatch, tmp_path: Path) -> None:
    controller = ComputerController(tmp_path)
    monkeypatch.setattr(controller, "_window", lambda _hwnd: {"hwnd": 7, "title": "Test", "bounds": {}})
    monkeypatch.setattr(controller, "elements", lambda *_args: {"elements": [{"name": "", "value": "RAVEN_OK", "control_type": "Edit", "visible": True}]})
    verified = controller.verify("Napiš přesně text RAVEN_OK do pole", 7, {"screen_changed": True})
    assert verified["achieved"] is True
    monkeypatch.setattr(controller, "elements", lambda *_args: {"elements": [{"name": "", "value": "jiný text"}]})
    rejected = controller.verify("Napiš přesně text RAVEN_OK do pole", 7, {"screen_changed": True})
    assert rejected["achieved"] is False


def test_exact_text_verifier_accepts_new_text_in_window_title(monkeypatch, tmp_path: Path) -> None:
    controller = ComputerController(tmp_path)
    monkeypatch.setattr(controller, "_window", lambda _hwnd: {"hwnd": 7, "title": "Editor | RAVEN_OK", "bounds": {}})
    monkeypatch.setattr(controller, "elements", lambda *_args: {"elements": []})
    verified = controller.verify(
        "Napiš přesně text RAVEN_OK do jediného textového pole",
        7,
        {"screen_changed": True, "window": {"title": "Editor"}},
    )
    assert verified["achieved"] is True
    assert verified["evidence"] == "window-title"


@pytest.mark.parametrize("element", [
    {"name": "RAVEN_OK", "control_type": "Button", "visible": True},
    {"value": "prefix RAVEN_OK suffix", "control_type": "Edit", "visible": True},
    {"value": "RAVEN_OK", "control_type": "Edit", "visible": False},
])
def test_text_verifier_rejects_labels_partial_and_hidden_values(monkeypatch, tmp_path, element):
    controller = ComputerController(tmp_path)
    monkeypatch.setattr(controller, "_window", lambda _: {"hwnd": 7})
    monkeypatch.setattr(controller, "elements", lambda *_: {"elements": [element]})
    assert not controller.verify("Napiš přesně text RAVEN_OK do pole", 7, {"screen_changed": True})["achieved"]


@pytest.mark.parametrize("changed", [True, False])
def test_screen_change_alone_never_proves_goal(monkeypatch, tmp_path, changed):
    controller = ComputerController(tmp_path)
    monkeypatch.setattr(controller, "_window", lambda _: {"hwnd": 7})
    result = controller.verify("Ulož dokument", 7, {"screen_changed": changed})
    assert result["achieved"] is False
    assert result["kind"] == "unverified"


@pytest.mark.skipif(os.environ.get("RAVEN_LIVE_COMPUTER_TEST") != "1", reason="explicit live GUI test")
def test_live_window_keyboard_and_screen_change() -> None:
    if not IS_WINDOWS:
        pytest.skip("Windows only")
    import subprocess
    import sys
    import time
    from uuid import uuid4

    token = "Raven-PC-test-" + uuid4().hex[:8]
    window_marker = "Raven Computer Control Test " + uuid4().hex[:8]
    script = (
        "import tkinter as tk; "
        f"r=tk.Tk(); r.title({window_marker!r}); r.geometry('640x240+120+120'); "
        "e=tk.Entry(r,font=('Segoe UI',18)); e.pack(fill='x',padx=30,pady=70); e.focus_force(); "
        f"e.bind('<KeyRelease>',lambda _e:r.title({window_marker!r}+' | '+e.get())); r.mainloop()"
    )
    process = subprocess.Popen([sys._base_executable, "-c", script])
    try:
        deadline = time.monotonic() + 12
        window = None
        while time.monotonic() < deadline:
            window = next((item for item in COMPUTER.windows() if window_marker in item["title"]), None)
            if window:
                break
            time.sleep(0.15)
        assert window is not None, "Testovací okno se neobjevilo"
        COMPUTER.reset_stop()
        result = COMPUTER.execute({
            "hwnd": window["hwnd"],
            "actions": [{"type": "type_text", "text": token}, {"type": "wait", "seconds": 0.25}],
        })
        deadline = time.monotonic() + 3
        changed_title = ""
        while time.monotonic() < deadline:
            changed_title = next((item["title"] for item in COMPUTER.windows() if window_marker in item["title"]), "")
            if token in changed_title:
                break
            time.sleep(0.1)
        assert result["success"] is True
        assert result["requested_count"] == 2
        assert token in changed_title
        assert Path(result["before"]["path"]).is_file()
        assert Path(result["after"]["path"]).is_file()
        assert COMPUTER.audit_path.is_file()
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.skipif(os.environ.get("RAVEN_LIVE_AI_COMPUTER_TEST") != "1", reason="explicit live local-model GUI test")
def test_live_local_planner_controls_real_window() -> None:
    if not IS_WINDOWS:
        pytest.skip("Windows only")
    import subprocess
    import sys
    import time
    from uuid import uuid4

    token = "RAVEN_AI_PC_CONTROL_OK_" + uuid4().hex[:6]
    window_marker = "Raven AI Computer Test " + uuid4().hex[:8]
    script = (
        "import tkinter as tk; "
        f"r=tk.Tk(); r.title({window_marker!r}); r.geometry('640x240+120+120'); "
        "e=tk.Entry(r,font=('Segoe UI',18)); e.pack(fill='x',padx=30,pady=70); e.focus_force(); "
        f"e.bind('<KeyRelease>',lambda _e:r.title({window_marker!r}+' | '+e.get())); r.mainloop()"
    )
    process = subprocess.Popen([sys._base_executable, "-c", script])
    try:
        deadline = time.monotonic() + 12
        window = None
        while time.monotonic() < deadline:
            window = next((item for item in COMPUTER.windows() if window_marker in item["title"]), None)
            if window:
                break
            time.sleep(0.15)
        assert window is not None, "Testovací okno se neobjevilo"
        plan = raven_control.plan_computer_task(
            f"Napiš přesně text {token} do jediného textového pole.",
            window["hwnd"],
            model="qwen3.5:0.8b",
        )
        assert plan["actions"], plan
        COMPUTER.reset_stop()
        result = COMPUTER.execute({"hwnd": window["hwnd"], "actions": plan["actions"]})
        deadline = time.monotonic() + 5
        changed_title = ""
        while time.monotonic() < deadline:
            changed_title = next((item["title"] for item in COMPUTER.windows() if window_marker in item["title"]), "")
            if token in changed_title:
                break
            time.sleep(0.1)
        assert result["success"] is True
        assert token in changed_title, {"plan": plan, "title": changed_title}
        assert result["before"]["sha256_pixels"] != result["after"]["sha256_pixels"]
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

import json
import os
from pathlib import Path

import pytest

from raven_builder import build_project, detect_application_request, parse_blueprint, safe_file_path


def test_builder_creates_and_validates_small_web_application(tmp_path: Path) -> None:
    blueprint = {
        "name": "Moje aplikace",
        "kind": "web",
        "files": [
            {"path": "index.html", "content": "<!doctype html><html><body><h1>Raven</h1><script src=\"app.js\"></script></body></html>"},
            {"path": "app.js", "content": "document.querySelector('h1').textContent = 'Hotovo';"},
            {"path": "data.json", "content": '{"ok":true}'},
        ],
        "test_instructions": ["Otevřít index.html"],
    }
    result = build_project("Vytvoř aplikaci", tmp_path / "app", lambda _prompt, _schema: json.dumps(blueprint))
    assert result["status"] == "created"
    assert result["validation"]["passed"] is True
    assert (tmp_path / "app" / "index.html").is_file()
    assert all(len(item["sha256"]) == 64 for item in result["files"])


@pytest.mark.parametrize("value", ["../secret.py", "C:/secret.py", "/absolute.js", "run.exe", "a/../../x.js", "app.js:secret.js", "NUL.py", "src./main.py", "src//main.py"])
def test_builder_rejects_unsafe_files(value: str) -> None:
    with pytest.raises(ValueError):
        safe_file_path(value)


def test_builder_removes_its_new_files_when_validation_fails(tmp_path: Path) -> None:
    blueprint = {"name": "bad", "kind": "web", "files": [{"path": "index.html", "content": "<html>broken"}]}
    destination = tmp_path / "bad"
    with pytest.raises(ValueError, match="neprošel"):
        build_project("Vytvoř aplikaci", destination, lambda _prompt, _schema: json.dumps(blueprint))
    assert list(destination.rglob("*")) == []


def test_application_detection_excludes_installer() -> None:
    assert detect_application_request('Vytvoř aplikaci "Počítadlo"') == {"action": "create_application", "name": "Počítadlo"}
    assert detect_application_request("Vytvoř instalační EXE aplikace") is None


def test_builder_never_deletes_existing_project_on_failure(tmp_path):
    original = tmp_path / "index.html"
    original.write_text("original user work")
    with pytest.raises(ValueError, match="není prázdná"):
        build_project("Vytvoř aplikaci", tmp_path, lambda *_: "invalid", overwrite=True)
    assert original.read_text() == "original user work"


def test_builder_preserves_files_created_while_model_is_working(tmp_path):
    destination = tmp_path / "app"
    def generate(*args):
        destination.mkdir()
        (destination / "index.html").write_text("user work")
        return json.dumps({"name": "app", "kind": "web", "files": [
            {"path": "index.html", "content": "<html><body>generated</body></html>"}]})
    with pytest.raises(ValueError, match="během generování změnila"):
        build_project("Create app", destination, generate)
    assert (destination / "index.html").read_text() == "user work"
    assert not list(tmp_path.glob(".raven-build-*"))


def test_builder_validates_before_publishing_any_files(tmp_path, monkeypatch):
    import raven_builder
    destination = tmp_path / "app"
    def validate(stage, blueprint, **kwargs):
        assert stage != destination
        assert not destination.exists()
        assert (stage / "index.html").is_file()
        return {"passed": True, "checks": [{"passed": True, "message": "fixture"}]}
    monkeypatch.setattr(raven_builder, "validate_project", validate)
    blueprint = {"name": "app", "kind": "web", "files": [{"path": "index.html", "content": "<html><body>ok</body></html>"}]}
    build_project("Create app", destination, lambda *_: json.dumps(blueprint))
    assert (destination / "index.html").is_file()
    assert not list(tmp_path.glob(".raven-build-*"))


def test_builder_repairs_invalid_first_attempt_without_publishing_it(tmp_path):
    attempts = []
    def generate(prompt, _schema):
        attempts.append(prompt)
        if len(attempts) == 1:
            return json.dumps({"name": "app", "kind": "web", "files": [{"path": "index.html", "content": "<html>broken"}]})
        return json.dumps({"name": "app", "kind": "web", "files": [{"path": "index.html", "content": "<html><body>fixed</body></html>"}]})
    result = build_project("Create app", tmp_path / "app", generate)
    assert result["attempts"] == 2
    assert "Předchozí návrh" in attempts[1]
    assert (tmp_path / "app" / "index.html").read_text(encoding="utf-8").endswith("</html>")


def test_web_blueprint_requires_index() -> None:
    with pytest.raises(ValueError, match="index.html"):
        parse_blueprint({"name": "x", "kind": "web", "files": [{"path": "app.js", "content": "1"}]})


@pytest.mark.skipif(os.environ.get("RAVEN_LIVE_BUILDER_TEST") != "1", reason="explicit live local-model builder test")
def test_live_local_model_builds_valid_web_application(tmp_path: Path) -> None:
    import raven_control

    result = build_project(
        "Vytvoř velmi malou offline webovou aplikaci Počítadlo se dvěma tlačítky plus a minus. Bez knihoven.",
        tmp_path / "live-app",
        lambda prompt, schema: raven_control.local_model_request(
            [{"role": "system", "content": "Vrať pouze JSON podle schématu."}, {"role": "user", "content": prompt}],
            "qwen3.5:4b",
            schema,
        ),
    )
    assert result["validation"]["passed"] is True
    assert (tmp_path / "live-app" / "index.html").is_file()

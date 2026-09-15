import json
import time
from pathlib import Path

from pydantic import BaseModel, Field

from raven_tools import ToolRegistry, ToolSpec


class EchoInput(BaseModel):
    text: str = Field(min_length=1, max_length=20)


def test_registry_validates_permissions_result_and_audit(tmp_path: Path) -> None:
    registry = ToolRegistry(tmp_path / "audit.jsonl")
    registry.register(ToolSpec(
        name="echo", description="Echo", input_model=EchoInput,
        executor=lambda value: {"text": value.text}, requires_confirmation=True,
        verifier=lambda output: (output["text"] == "ok", "read-back"),
    ))
    denied = registry.execute("echo", {"text": "ok"}, permission_mode="confirm")
    assert denied.status == "failed"
    result = registry.execute("echo", {"text": "ok"}, permission_mode="confirm", confirmed=True)
    assert result.status == "completed" and result.verified is True
    rows = [json.loads(row) for row in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["status"] for row in rows] == ["failed", "completed"]


def test_registry_normalizes_validation_and_timeout(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(ToolSpec(name="slow", description="Slow", input_model=EchoInput,
                               executor=lambda _value: (time.sleep(1.1) or {}), timeout_seconds=1))
    assert registry.execute("slow", {"text": "x"}, permission_mode="full").status == "failed"
    invalid = registry.execute("slow", {"text": ""}, permission_mode="full")
    assert invalid.status == "failed"


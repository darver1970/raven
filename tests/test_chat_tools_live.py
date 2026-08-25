"""Zivy prijimaci test propojeni chatu se souborovym agentem."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import pytest

from raven_intelligence import known_desktop


API = "http://127.0.0.1:8126"


def request(path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    method = "GET" if payload is None else "POST"
    req = urllib.request.Request(API + path, data=data, headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def test_raven_chat_really_creates_and_verifies_desktop_file() -> None:
    if os.environ.get("RAVEN_LIVE_TEST") != "1":
        pytest.skip("Živý test vyžaduje spuštěný Raven a RAVEN_LIVE_TEST=1.")
    target: Path = known_desktop() / "test.txt"
    if target.exists():
        pytest.skip("Na ploše už existuje uživatelův test.txt; nesmí být přepsán.")
    request("/settings", {"permission_mode": "full", "simulation_mode": False})
    try:
        response = request("/chat", {
            "model": "automatic",
            "simulate": False,
            "messages": [{"role": "user", "content": 'Vytvoř soubor test.txt na ploše s obsahem "Raven live test"'}],
        })
        assert response["provider"] == "local-tool"
        assert target.is_file()
        assert target.read_text(encoding="utf-8") == "Raven live test"
        events = request("/events/recent")["events"]
        steps = [item["step"] for item in events[-12:]]
        for required in ("plan", "execute", "test", "review", "done"):
            assert required in steps
    finally:
        target.unlink(missing_ok=True)
    assert not target.exists()

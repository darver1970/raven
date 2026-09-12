"""End-to-end smoke test for an installed Raven Electron application."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import time
import urllib.request
import urllib.error

from playwright.sync_api import Browser, Page, sync_playwright


def request_json(url: str, payload: dict[str, object] | None = None, timeout: int = 180) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = json.loads(error.read().decode("utf-8"))
        raise AssertionError(f"HTTP {error.code}: {detail.get('error', 'unknown error')}") from error


def wait_for_hud(browser: Browser, timeout_seconds: int = 300) -> Page:
    deadline = time.monotonic() + timeout_seconds
    discovery = browser.new_browser_cdp_session()
    while time.monotonic() < deadline:
        # Pump protocol events while the packaged app creates its first window.
        discovery.send("Target.getTargets")
        page_candidates = [page for context in browser.contexts for page in context.pages]
        hud = next(
            (page for page in page_candidates if page.url.startswith("http://127.0.0.1:5174/")),
            None,
        )
        if hud is not None:
            discovery.detach()
            return hud
        time.sleep(0.25)
    discovery.detach()
    raise AssertionError(f"Nainstalovaný Raven nenačetl HUD do {timeout_seconds} sekund.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--cdp-port", required=True, type=int)
    parser.add_argument("--screenshot", required=True, type=Path)
    parser.add_argument("--skip-model", action="store_true", help="Only validate UI/tools; report inference as skipped")
    arguments = parser.parse_args()

    root = arguments.root.resolve()
    desktop_executable_exists = (root / "Raven.exe").is_file() or (root / "desktop" / "Raven-Desktop.exe").is_file()
    if not desktop_executable_exists or not (root / "raven_control.py").is_file():
        raise FileNotFoundError(f"Neplatná instalace Raven: {root}")
    test_file = root / "raven-installed-agent-test.txt"
    if test_file.exists():
        raise FileExistsError(f"Test nesmí přepsat existující soubor: {test_file}")
    arguments.screenshot.parent.mkdir(parents=True, exist_ok=True)

    browser = None
    hud = None
    errors: list[str] = []
    settings_before = None
    test_settings = {"permission_mode": "full", "simulation_mode": False, "ai_provider": "local"}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{arguments.cdp_port}")
            hud = wait_for_hud(browser)
            print("HUD connected", flush=True)
            hud.on("pageerror", lambda error: errors.append(str(error)))
            hud.reload(wait_until="domcontentloaded")
            hud.wait_for_timeout(1200)
            hud.evaluate("document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close())")

            assert hud.title() == "Raven 1.2"
            assert hud.locator(".app-menu-brand").text_content().strip() == "Raven 1.2"
            assert hud.evaluate("Boolean(window.ravenDesktop)") is True
            installed_root = Path(hud.evaluate("window.ravenDesktop.rootPath()")).resolve()
            assert installed_root == root
            print("Application root verified", flush=True)
            files = hud.evaluate("path => window.ravenDesktop.listFiles({path})", str(root))
            assert any(item["name"] == "raven_control.py" for item in files["entries"])

            terminal_state = hud.evaluate("window.ravenDesktop.terminal.create({})")
            terminal_id = terminal_state["terminals"][-1]["id"]
            hud.evaluate(
                "id => window.ravenDesktop.terminal.write({id, data: 'Write-Output RAVEN_INSTALLED_TERMINAL_OK', permissionMode: 'full', confirmed: true})",
                terminal_id,
            )
            hud.wait_for_function(
                "id => String(terminalBuffers.get(id) || '').includes('RAVEN_INSTALLED_TERMINAL_OK')",
                arg=terminal_id,
                timeout=15000,
            )
            hud.wait_for_function(
                "id => String(terminalBuffers.get(id) || '').includes('[exit 0]')",
                arg=terminal_id,
                timeout=15000,
            )
            hud.evaluate("id => window.ravenDesktop.terminal.close({id})", terminal_id)
            print("Terminal passed", flush=True)

            hud.locator('[data-view="agents"]').first.click()
            hud.locator("#agent-tree").get_by_text("Memory Manager", exact=True).wait_for(state="attached", timeout=30000)
            assert hud.locator("#agent-tree").get_by_text("Memory Manager", exact=True).count() == 1
            assert hud.locator("#agent-tree").get_by_text("Installer", exact=True).count() == 1
            assert hud.locator("#agent-tree").get_by_text("Analytik", exact=True).count() == 1
            hud.locator('[data-view="telemetry"]').first.click()
            hud.wait_for_timeout(2200)
            assert hud.locator(".disk-card").count() >= 1
            assert hud.locator(".disk-bar i").count() >= 1
            assert hud.locator(".telemetry-chart").count() == 5

            settings_before = request_json("http://127.0.0.1:8126/settings")
            request_json(
                "http://127.0.0.1:8126/settings",
                test_settings,
            )
            create_result = request_json(
                "http://127.0.0.1:8126/chat",
                {
                    "model": "qwen3.5:4b",
                    "simulate": False,
                    "messages": [
                        {
                            "role": "user",
                            "content": f'Vytvoř soubor "{test_file}" s obsahem "Raven installed agent OK"',
                        }
                    ],
                },
            )
            assert create_result["provider"] == "local-tool"
            assert test_file.read_text(encoding="utf-8") == "Raven installed agent OK"

            answer_result = {"answer": "SKIPPED (no model installed)", "provider": "skipped"} if arguments.skip_model else request_json(
                "http://127.0.0.1:8126/chat",
                {
                    "model": "qwen3.5:4b",
                    "simulate": False,
                    "messages": [{"role": "user", "content": "Kolik je 17 + 25? Odpověz pouze číslem."}],
                },
            )
            answer = str(answer_result.get("answer", "")).strip()
            if not arguments.skip_model:
                assert answer_result["provider"] == "local"
                assert re.search(r"(?<!\d)42(?!\d)", answer), answer
                assert answer_result.get("review", {}).get("accepted") is True

            delete_result = request_json(
                "http://127.0.0.1:8126/chat",
                {
                    "model": "automatic",
                    "simulate": False,
                    "confirmed": True,
                    "messages": [{"role": "user", "content": f'Smaž soubor "{test_file}"'}],
                },
            )
            assert delete_result["provider"] == "local-tool"
            assert not test_file.exists()

            hud.locator('[data-view="chat"]').first.click()
            hud.wait_for_timeout(300)
            hud.screenshot(path=str(arguments.screenshot), full_page=True)
            if errors:
                raise AssertionError(json.dumps(errors, ensure_ascii=False, indent=2))
            print(
                json.dumps(
                    {
                        "ok": True,
                        "root": str(root),
                        "terminal": "passed",
                        "file_agent": "create-readback-delete passed",
                        "local_model": answer,
                        "review": "skipped" if arguments.skip_model else "accepted",
                        "screenshot": str(arguments.screenshot),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            try:
                if settings_before is not None:
                    current = request_json("http://127.0.0.1:8126/settings")
                    restore = {key: settings_before[key] for key, value in test_settings.items()
                               if key in settings_before and current.get(key) == value}
                    if restore:
                        request_json("http://127.0.0.1:8126/settings", restore)
                    settings_before = None
                hud.evaluate("window.ravenDesktop.close()")
            except Exception as error:
                if "closed" not in str(error).lower() and "destroyed" not in str(error).lower():
                    raise
    finally:
        if settings_before is not None:
            current = request_json("http://127.0.0.1:8126/settings")
            restore = {key: settings_before[key] for key, value in test_settings.items()
                       if key in settings_before and current.get(key) == value}
            if restore:
                request_json("http://127.0.0.1:8126/settings", restore)
        if test_file.exists():
            test_file.unlink()


if __name__ == "__main__":
    main()

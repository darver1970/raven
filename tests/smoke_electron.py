"""Smoke test the real Electron UI through local CDP."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import time

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "runtime" / "test-results"
RESULTS.mkdir(parents=True, exist_ok=True)
SCREENSHOT = RESULTS / "raven-main.png"


with sync_playwright() as playwright:
    browser = playwright.chromium.connect_over_cdp("http://127.0.0.1:9223")
    hud = None
    page_deadline = time.monotonic() + 30
    while time.monotonic() < page_deadline:
        pages = [page for context in browser.contexts for page in context.pages]
        hud = next(
            (page for page in pages if page.url.startswith("http://127.0.0.1:5174/")),
            None,
        )
        if hud is not None:
            break
        time.sleep(0.25)
    if hud is None:
        raise AssertionError("Raven HUD se v zabaleném Electronu nenačetl do 30 sekund.")
    errors: list[str] = []
    hud.on("pageerror", lambda error: errors.append(str(error)))
    hud.reload(wait_until="domcontentloaded")
    hud.wait_for_timeout(1200)
    hud.evaluate("document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close())")
    assert hud.title() == "Raven 1.0"
    assert hud.locator(".app-menu-brand").text_content().strip() == "Raven 1.0"
    assert hud.locator(".app-menu").count() == 3
    assert hud.evaluate("Boolean(window.ravenDesktop)") is True
    assert hud.locator("#composer-access").input_value() == "full"
    assert hud.locator("#composer-simulate").get_attribute("aria-pressed") in {"true", "false"}
    assert hud.locator("#change-card").evaluate("node => node.classList.contains('hidden')") is True
    assert hud.locator("#change-card").evaluate("node => node.parentElement.id") == "messages"
    assert "Groq Free" in hud.locator("#composer-provider").text_content()
    drives = hud.evaluate("window.ravenDesktop.listFiles({path: '::drives'})")
    assert any(item["path"].upper().startswith("C:") for item in drives["entries"])
    files = hud.evaluate("window.ravenDesktop.listFiles({path: 'C:\\\\projektjarvis'})")
    assert any(item["name"] == "raven_control.py" for item in files["entries"])
    source = hud.evaluate("window.ravenDesktop.readFile('C:\\\\projektjarvis\\\\raven_control.py')")
    assert "RAVEN_SYSTEM_PROMPT" in source["content"]
    version_file = hud.evaluate("window.ravenDesktop.readFile('C:\\\\projektjarvis\\\\VERSION')")
    assert version_file["content"].strip() in {"1.0", "v1.0"}
    first = hud.evaluate("window.ravenDesktop.browser.list()")
    assert len(first["tabs"]) >= 1
    second = hud.evaluate("window.ravenDesktop.browser.create('https://example.com/')")
    assert len(second["tabs"]) >= 2
    active = second["activeTabId"]
    hud.evaluate("id => window.ravenDesktop.browser.close(id)", active)
    closed = hud.evaluate("window.ravenDesktop.browser.list()")
    assert len(closed["tabs"]) >= 1
    hud.locator('[data-view="agents"]').first.click()
    hud.wait_for_timeout(400)
    assert hud.get_by_text("Memory Manager", exact=True).count() == 1
    assert hud.get_by_text("Project Indexer", exact=True).count() == 1
    assert hud.locator("#agent-tree").get_by_text("Analytik", exact=True).count() == 1
    hud.locator('[data-view="web"]').first.click()
    hud.wait_for_timeout(500)
    assert hud.locator(".native-browser-surface").count() >= 1
    hud.locator('[data-view="telemetry"]').first.click()
    hud.wait_for_timeout(2200)
    assert hud.locator("#task-progress").evaluate("node => node.classList.contains('hidden')") is True
    assert hud.locator(".disk-card").count() >= 1
    assert hud.locator(".disk-bar i").count() >= 1
    assert hud.locator(".telemetry-chart").count() == 5
    hud.locator('[data-view="settings"]').first.click()
    hud.wait_for_timeout(300)
    assert hud.locator("#library-locations").count() == 1
    assert hud.locator("#add-library-folder").count() == 1
    assert hud.locator("#run-diagnostics").count() == 1
    hud.locator('[data-view="chat"]').first.click()
    hud.wait_for_timeout(300)
    assert hud.locator("#task-progress").evaluate("node => node.classList.contains('hidden')") is True
    hud.evaluate("""async () => {
      await fetch('http://127.0.0.1:8126/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({permission_mode:'full', simulation_mode:true})});
      await fetch('http://127.0.0.1:8126/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({simulate:true, model:'automatic', messages:[{role:'user', content:'Vytvoř soubor raven-ui-live-log-test.txt na ploše'}]})});
    }""")
    hud.wait_for_selector(".live-work-log article", timeout=5000)
    hud.wait_for_timeout(500)
    work_log = hud.locator(".live-work-log")
    assert work_log.locator("article").count() >= 5
    assert work_log.locator(":scope > div").evaluate("node => getComputedStyle(node).maxHeight") == "none"
    assert work_log.locator(":scope > div").evaluate("node => getComputedStyle(node).overflowY") == "visible"
    assert "Planner" in work_log.text_content() or "planner" in work_log.text_content()
    assert "Files" in work_log.text_content() or "files" in work_log.text_content()
    assert "Úkol dokončen" in work_log.text_content()
    hud.wait_for_timeout(2800)
    assert hud.locator(".live-work-log").count() == 0
    hud.evaluate("""() => {
      state.liveEventChatId = 'smoke-source-chat';
      state.activeChatId = 'smoke-target-chat';
      state.liveEvents = [{id:'chat-isolation-test', step:'analysis', status:'working', agent:'analyst', result:'Kontrolní průběh'}];
      renderWorkLog();
    }""")
    assert hud.locator(".live-work-log").count() == 0
    hud.evaluate("""async () => {
      await fetch('http://127.0.0.1:8126/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({permission_mode:'full', simulation_mode:false})});
    }""")
    hud.screenshot(path=str(SCREENSHOT), full_page=True)
    summary = hud.evaluate("window.ravenDesktop.gitSummary()")
    assert summary["count"] > 0
    if errors:
        raise AssertionError(json.dumps(errors, ensure_ascii=False, indent=2))
    print(json.dumps({"ok": True, "tabs": len(closed["tabs"]), "files": len(files["entries"]), "agents": 2, "git_changes": summary["count"], "screenshot": str(SCREENSHOT)}, ensure_ascii=False))
    try:
        browser.new_browser_cdp_session().send("Browser.close")
    except Exception as error:
        if "closed" not in str(error).lower():
            raise

for _ in range(30):
    try:
        SCREENSHOT.unlink(missing_ok=True)
        break
    except PermissionError:
        time.sleep(0.2)

profile_value = os.environ.get("RAVEN_ELECTRON_PROFILE", "").strip()
if profile_value:
    profile = Path(profile_value).resolve()
    runtime_root = (ROOT / "runtime").resolve()
    if profile.parent != runtime_root or not profile.name.startswith("electron-smoke-"):
        raise RuntimeError(f"Odmítám uklidit neplatný testovací profil: {profile}")
    for attempt in range(30):
        try:
            if profile.exists():
                shutil.rmtree(profile)
            break
        except PermissionError:
            if attempt == 29:
                raise
            time.sleep(0.2)

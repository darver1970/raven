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
    cdp_port = int(os.environ.get("RAVEN_DEBUG_PORT", "9223"))
    browser = None
    connect_error = None
    connect_deadline = time.monotonic() + 60
    while time.monotonic() < connect_deadline:
        try:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
            break
        except Exception as error:
            connect_error = error
            time.sleep(0.5)
    if browser is None:
        raise AssertionError(f"Electron CDP na portu {cdp_port} nebyl stabilně dostupný: {connect_error}")
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
    hud.wait_for_function("state.providers.length > 0 && state.settings.provider_order.length > 0", timeout=15000)
    hud.evaluate("document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close())")
    assert hud.title() == "Raven 1.2"
    assert hud.locator(".app-menu-brand").text_content().strip() == "Raven 1.2"
    assert hud.locator(".app-menu").count() == 3
    assert hud.evaluate("Boolean(window.ravenDesktop)") is True
    assert hud.locator("#composer-access").input_value() in {"full", "confirm", "denied"}
    assert hud.locator("#composer-simulate").get_attribute("aria-pressed") in {"true", "false"}
    assert hud.locator("#change-card").evaluate("node => node.classList.contains('hidden')") is True
    assert hud.locator("#change-card").evaluate("node => node.parentElement.id") == "messages"
    hud.locator("#open-command-palette").click()
    assert hud.locator("#command-palette-dialog").evaluate("node => node.open") is True
    hud.locator("#command-palette-search").fill("telemetrie")
    assert hud.locator("#command-palette-results .palette-item").count() >= 1
    hud.locator("#command-palette-search").press("Escape")
    hud.wait_for_function("!document.querySelector('#command-palette-dialog').open")
    for workspace, expected_title in (
        ("output", "Výstupy"),
        ("logs", "Logy"),
        ("memory", "Paměť"),
        ("artifacts", "Artefakty"),
    ):
        hud.locator(f'[data-workspace="{workspace}"]').click()
        hud.wait_for_timeout(150)
        actual_title = hud.locator("#workspace-title").text_content().strip()
        assert actual_title == expected_title, (workspace, expected_title, actual_title)
        assert hud.locator(f'[data-workspace="{workspace}"].active').count() == 1
    hud.locator('[data-workspace="memory"]').click()
    hud.locator("#add-memory").wait_for(state="visible")
    hud.locator("#add-memory").click()
    assert hud.locator("#memory-dialog").evaluate("node => node.open") is True
    hud.locator("#cancel-memory").click()
    assert hud.locator("#memory-dialog").evaluate("node => node.open") is False
    hud.locator('[data-view="chat"]').first.click()
    hud.evaluate("""() => {
        window.__qaPriorEvents = state.liveEvents;
        window.__qaPriorEventChat = state.liveEventChatId;
        renderLiveEvent({id:'qa-unverified', step:'needs_verification',
            status:'needs_verification', chat_id:state.activeChatId,
            result:'Úkol není ověřen jako dokončený'});
    }""")
    assert hud.locator(".live-work-log").inner_text().find("Vyžaduje další ověření") >= 0
    assert not hud.locator('#task-progress [data-step="done"]').evaluate("node => node.classList.contains('active')")
    hud.evaluate("""() => {
        state.liveEvents = window.__qaPriorEvents;
        state.liveEventChatId = window.__qaPriorEventChat;
        delete window.__qaPriorEvents; delete window.__qaPriorEventChat;
        renderWorkLog(); setProgress('Připraven');
    }""")
    assert "Groq Free" in hud.locator("#composer-provider").text_content()
    hud.evaluate("""async () => {
      document.querySelector('#composer-access').value = 'full';
      await fetch('http://127.0.0.1:8126/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({permission_mode:'full'})});
    }""")
    hud.locator("#add-project").click()
    assert hud.locator("#project-dialog").evaluate("node => node.open") is True
    hud.locator("#cancel-project").click()
    assert hud.locator("#project-dialog").evaluate("node => node.open") is False
    hud.locator('[data-view="agents"]').first.click()
    hud.locator("#add-agent").click()
    assert hud.locator("#agent-dialog").evaluate("node => node.open") is True
    hud.locator("#cancel-agent").click()
    assert hud.locator("#agent-dialog").evaluate("node => node.open") is False
    hud.evaluate("openMemoryDialog()")
    assert hud.locator("#memory-dialog").evaluate("node => node.open") is True
    hud.locator("#cancel-memory-x").click()
    assert hud.locator("#memory-dialog").evaluate("node => node.open") is False
    hud.locator('[data-view="chat"]').first.click()
    hud.locator("#add-project").click()
    project_name = f"Raven smoke {int(time.time())}"
    hud.locator("#project-form [name='name']").fill(project_name)
    hud.locator("#save-project").click()
    hud.wait_for_function(
        "name => [...document.querySelectorAll('[data-project]')].some(node => node.textContent.includes(name))",
        arg=project_name,
    )
    project_row = hud.locator(".project-entry", has_text=project_name)
    assert project_row.locator("[data-delete-project]").count() == 1
    project_row.locator("[data-delete-project]").click()
    hud.wait_for_function(
        "name => ![...document.querySelectorAll('[data-project]')].some(node => node.textContent.includes(name))",
        arg=project_name,
    )
    hud.evaluate("""() => {
      state.chats = [{id:'smoke-ui-chat', title:'UI test', messages:[
        {id:'11111111111111111111111111111111', role:'user', content:'Původní dotaz', created_at:new Date().toISOString()},
        {id:'22222222222222222222222222222222', role:'assistant', content:'Původní odpověď', created_at:new Date().toISOString(), feedback:{id:'33333333333333333333333333333333', rating:1, approved_for_training:false}}
      ]}];
      state.activeChatId = 'smoke-ui-chat';
      renderMessages();
    }""")
    assert hud.locator("[data-edit-message]").count() == 1
    assert hud.locator("[data-feedback='1'].feedback-selected").count() == 1
    hud.evaluate("""async () => {
      state.running = true;
      document.querySelector('#command').value = 'Zaměř se hlavně na ovládání PC';
      await sendMessage();
    }""")
    assert "ovládání PC" in hud.locator("#steering-text").text_content()
    assert hud.locator("#steering-bar").evaluate("node => !node.classList.contains('hidden')") is True
    hud.locator("#edit-steering").click()
    assert "ovládání PC" in hud.locator("#command").input_value()
    assert hud.locator("#steering-bar").evaluate("node => node.classList.contains('hidden')") is True
    hud.evaluate("state.running = false; document.querySelector('#command').value = ''; renderMessages()")
    hud.locator('[data-view="settings"]').first.click()
    hud.wait_for_timeout(400)
    assert hud.locator("#online-provider-ack").count() == 1
    assert hud.locator("#key-provider option").count() == 7
    assert "Gemini Free" in hud.locator("#key-provider").text_content()
    assert "Groq Free" in hud.locator("#key-provider").text_content()
    assert "Cloudflare Workers AI Free" in hud.locator("#key-provider").text_content()
    provider_order_rows = hud.locator(".provider-order-row").count()
    assert provider_order_rows == 8, provider_order_rows
    assert "Data neopouštějí počítač" in hud.locator(".provider-status-grid").text_content()
    drives = hud.evaluate("window.ravenDesktop.listFiles({path: '::drives'})")
    assert any(item["path"].upper().startswith("C:") for item in drives["entries"])
    root_path = hud.evaluate("window.ravenDesktop.rootPath()")
    files = hud.evaluate("path => window.ravenDesktop.listFiles({path})", root_path)
    assert any(item["name"] == "raven_control.py" for item in files["entries"])
    source = hud.evaluate(
        "path => window.ravenDesktop.readFile(`${path}\\\\raven_control.py`)",
        root_path,
    )
    assert "RAVEN_SYSTEM_PROMPT" in source["content"]
    version_file = hud.evaluate(
        "path => window.ravenDesktop.readFile(`${path}\\\\VERSION`)",
        root_path,
    )
    assert version_file["content"].strip() in {"1.2", "v1.2", "1.2.1", "v1.2.1", "1.2.2", "v1.2.2", "1.2.3", "v1.2.3", "1.2.4", "v1.2.4"}
    terminal_state = hud.evaluate("window.ravenDesktop.terminal.create({})")
    terminal_id = terminal_state["terminals"][-1]["id"]
    hud.evaluate(
        "id => window.ravenDesktop.terminal.write({id, data: 'Write-Output RAVEN_TERMINAL_OK', permissionMode: 'full', confirmed: true})",
        terminal_id,
    )
    hud.wait_for_function(
        "id => String(terminalBuffers.get(id) || '').includes('RAVEN_TERMINAL_OK')",
        arg=terminal_id,
        timeout=10000,
    )
    hud.wait_for_function(
        "id => String(terminalBuffers.get(id) || '').includes('[exit 0]')",
        arg=terminal_id,
        timeout=10000,
    )
    hud.evaluate("id => window.ravenDesktop.terminal.close({id})", terminal_id)
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
    hud.locator("#agent-tree").get_by_text("Memory Manager", exact=True).wait_for(state="attached", timeout=30000)
    assert hud.locator("#agent-tree").get_by_text("Memory Manager", exact=True).count() == 1
    assert hud.get_by_text("Project Indexer", exact=True).count() == 1
    assert hud.locator("#agent-tree").get_by_text("Analytik", exact=True).count() == 1
    assert hud.get_by_text("Goal Manager", exact=True).count() == 1
    assert hud.get_by_text("Installer", exact=True).count() == 1
    hud.locator('[data-view="web"]').first.click()
    hud.wait_for_timeout(500)
    assert hud.locator(".native-browser-surface").count() >= 1
    hud.locator('[data-view="telemetry"]').first.click()
    hud.wait_for_timeout(2200)
    assert hud.locator("#task-progress").evaluate("node => node.classList.contains('hidden')") is True
    assert hud.locator(".disk-card").count() >= 1
    assert hud.locator(".disk-bar i").count() >= 1
    assert hud.locator(".telemetry-chart").count() == 5
    assert hud.evaluate("state.hardware.online") is True
    assert hud.evaluate("Number(state.hardware.performance?.cpu_clock_mhz || 0) > 0") is True
    assert hud.evaluate("Number(state.hardware.performance?.ram_total_gb || 0) > 0") is True
    assert hud.evaluate("Number(state.hardware.performance?.cpu_logical_cores || 0) > 0") is True
    assert hud.evaluate("state.hardware.availability?.disks?.available === true") is True
    assert hud.locator("#telemetry-summary").get_by_text("RAM celkem", exact=True).count() == 1
    assert hud.locator("#telemetry-summary").get_by_text("Doba běhu", exact=True).count() == 1
    hud.locator('[data-view="computer"]').first.click()
    hud.wait_for_function("document.querySelector('#computer-screen.ready') && document.querySelectorAll('.computer-window').length > 0", timeout=15000)
    assert "Připraveno" in hud.locator("#computer-status").text_content()
    assert hud.locator("#computer-screen").get_attribute("src").startswith("http://127.0.0.1:8126/computer/screenshot")
    assert hud.locator("#computer-task-form").count() == 1
    assert hud.locator("#computer-stop").count() == 1
    hud.locator('[data-view="settings"]').first.click()
    hud.wait_for_timeout(300)
    assert hud.locator("#library-locations").count() == 1
    assert hud.locator("#add-library-folder").count() == 1
    assert hud.locator("#run-diagnostics").count() == 1
    assert hud.locator(".provider-status-card").count() >= 9
    assert hud.locator("#codex-status").count() == 1
    assert "Codex" not in hud.locator("#key-provider").text_content()
    hud.locator("#open-telemetry-settings").click()
    hud.locator("#start-hardware-sensors").wait_for(state="visible")
    assert "UAC" in hud.locator("#hardware-sensor-result").text_content()
    hud.locator("#telemetry-dialog button[value='cancel']").first.click()
    assert hud.locator("#telemetry-dialog").evaluate("node => node.open") is False
    hud.locator('[data-view="capabilities"]').first.click()
    hud.wait_for_function("document.querySelectorAll('.capability-card').length >= 32", timeout=10000)
    assert hud.locator("#view-capabilities.active").count() == 1
    assert hud.locator(".capability-summary article").count() == 6
    assert hud.locator(".capability-card").count() >= 32
    assert hud.locator("#v12-settings-form [name='safe_mode']").count() == 1
    assert hud.locator("#v12-settings-form [name='high_contrast']").count() == 1
    assert hud.locator("#mcp-quick-form").count() == 1
    assert hud.locator("#create-review-workflow").count() == 1
    assert hud.locator("#cortex-status").count() == 1
    assert hud.locator("#training-run-eval").count() == 1
    assert hud.locator("#training-export").count() == 1
    assert abs(hud.evaluate("window.ravenDesktop.zoom(1.25)") - 1.25) < 0.01
    assert abs(hud.evaluate("window.ravenDesktop.zoom(1)") - 1) < 0.01
    hud.locator('[data-view="chat"]').first.click()
    hud.wait_for_timeout(300)
    assert hud.locator("#task-progress").evaluate("node => node.classList.contains('hidden')") is True
    hud.evaluate("""async () => {
      await fetch('http://127.0.0.1:8126/settings', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({permission_mode:'full', simulation_mode:true})});
      await fetch('http://127.0.0.1:8126/chat', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({simulate:true, model:'automatic', messages:[{role:'user', content:'Vytvoř soubor raven-ui-live-log-test.txt na ploše'}]})});
    }""")
    hud.wait_for_selector(".live-work-log article", timeout=5000)
    hud.wait_for_function("document.querySelector('.live-work-log')?.textContent.includes('Úkol dokončen')", timeout=10000)
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
    print(json.dumps({"ok": True, "tabs": len(closed["tabs"]), "files": len(files["entries"]), "agents": 2, "git_changes": summary["count"], "screenshot": str(SCREENSHOT)}, ensure_ascii=False), flush=True)
    try:
        hud.evaluate("window.ravenDesktop.close()")
    except Exception as error:
        if "closed" not in str(error).lower() and "destroyed" not in str(error).lower():
            raise

profile_value = os.environ.get("RAVEN_ELECTRON_PROFILE", "").strip()
if profile_value:
    profile = Path(profile_value).resolve()
    runtime_root = (ROOT / "runtime").resolve()
    if profile.parent != runtime_root or not profile.name.startswith("electron-smoke-"):
        raise RuntimeError(f"Odmítám uklidit neplatný testovací profil: {profile}")
    for attempt in range(80):
        try:
            if profile.exists():
                shutil.rmtree(profile)
            break
        except PermissionError:
            # Electron může po zavření okna ještě držet cache. Finální úklid
            # provede runner až po zastavení všech procesů.
            if attempt == 79:
                break
            time.sleep(0.25)

"use strict";

const desktop = window.ravenDesktop || null;
let browserState = { activeTabId: "", tabs: [] };
let editor = null;
let editorModel = null;
const openFiles = [];
const fileNavigation = { history: [], index: -1 };
const agentBranches = ["Core", "Planning", "Research", "Browser", "Coding", "Testing", "Files", "Memory", "Security", "Tools", "Quality", "Automation", "System", "Release", "Extensions"];
const collapsedBranches = new Set(JSON.parse(localStorage.getItem("raven-collapsed-branches") || "[]"));
let changeBaseline = null;
let lastChangeSignature = "";
let changeCardDismissed = true;
let changeCardHideTimer = null;
let liveWorkHideTimer = null;
let terminalState = { terminals: [] };
let activeTerminalId = "";
const terminalBuffers = new Map();
let selectedFilePath = "";
let selectedSnapshotName = "";

if (desktop) {
  api = () => ({
    close: desktop.close,
    open_folder: desktop.openFolder,
    open_telemetry_window: desktop.openTelemetryWindow,
    open_agents_window: desktop.openAgentsWindow,
    list_files: desktop.listFiles,
    read_file: desktop.readFile,
    write_file: desktop.writeFile,
    git_status: desktop.gitStatus,
    git_diff: desktop.gitDiff,
    create_snapshot: desktop.createSnapshot,
    zoom: desktop.zoom
  });
}

setProgress = label => {
  const map = { "Připraven": "received", "Analyzuji": "analysis", "Plánuji": "plan", "Provádím": "execute", "Kontroluji": "review", "Hotovo": "done", "Chyba": "error" };
  const order = ["received", "analysis", "plan", "context", "execute", "edit", "test", "review", "done"];
  const target = order.indexOf(map[label] || label);
  $$("#task-progress span").forEach((node, index) => {
    node.classList.toggle("active", index === target);
    node.classList.toggle("done", target >= 0 && index < target);
    node.classList.toggle("error", label === "Chyba" && index === Math.max(0, target));
  });
};

function renderLiveEvent(event) {
  if (event.chat_id && state.activeChatId && event.chat_id !== state.activeChatId) return;
  clearTimeout(liveWorkHideTimer);
  liveWorkHideTimer = null;
  const order = ["received", "analysis", "plan", "context", "execute", "edit", "test", "review", "done"];
  const target = order.indexOf(event.step);
  $$("#task-progress span").forEach((node, index) => {
    node.classList.toggle("active", index === target);
    node.classList.toggle("done", target >= 0 && index < target);
    node.title = index === target ? [event.agent, event.model, event.tool, event.result, event.error].filter(Boolean).join(" · ") : "";
  });
  if (event.step === "received") { state.liveEvents = []; state.liveEventChatId = event.chat_id || state.activeChatId; }
  state.liveEvents = [...(state.liveEvents || []).filter(item => item.id !== event.id), event].slice(-18);
  renderWorkLog();
  if (event.result || event.error) notify(`${event.agent || "Raven"} · ${event.result || event.error}`, event.status === "error" ? "error" : "info");
  const statusAgent = $("#status-agent");
  const statusRoute = $("#status-route");
  if (statusAgent) statusAgent.textContent = `${event.agent || "Raven"} · ${event.status || "pracuje"}`;
  if (statusRoute) statusRoute.textContent = [event.step, event.tool, event.model].filter(Boolean).join(" · ") || "Směrování: čeká";
  if (event.step === "done" || event.status === "error") {
    get("/agents").then(payload => { state.agents = payload.agents || []; if (document.querySelector("#view-agents.active")) renderAgents(); }).catch(() => {});
    liveWorkHideTimer = setTimeout(() => { state.liveEvents = []; renderWorkLog(); liveWorkHideTimer = null; }, 2500);
  }
}

function renderWorkLog() {
  const host = $("#messages");
  if (!host) return;
  host.querySelector(".live-work-log")?.remove();
  if (state.liveEventChatId && state.liveEventChatId !== state.activeChatId) return;
  const values = state.liveEvents || [];
  if (!values.length) return;
  const labels = {received:"Požadavek přijat",analysis:"Analyzuji zadání",plan:"Připravuji plán",context:"Hledám souvislosti",execute:"Provádím akci",edit:"Upravuji soubory",test:"Ověřuji výsledek",review:"Kontroluji práci",done:"Úkol dokončen",error:"Chyba"};
  const section = document.createElement("section");
  section.className = "live-work-log";
  section.innerHTML = `<header><span></span><b>Průběh práce</b><small>${values.length} kroků</small></header><div>${values.map(item => `<article class="${item.status === "error" ? "error" : item.status === "completed" ? "completed" : "working"}"><i></i><span><b>${escapeHtml(labels[item.step] || item.step)}</b><small>${escapeHtml(item.agent || "Raven")}${item.tool ? ` · ${escapeHtml(item.tool)}` : ""}${item.model ? ` · ${escapeHtml(item.model)}` : ""}</small><p>${escapeHtml(item.error || item.result || "Pracuji…")}</p></span></article>`).join("")}</div>`;
  const finalAnswer = [...host.querySelectorAll(".message.assistant")].at(-1);
  if (finalAnswer && values.some(item => item.step === "done" || item.status === "error")) host.insertBefore(section, finalAnswer);
  else host.insertBefore(section, host.querySelector("#change-card"));
  $("#empty-chat").classList.add("hidden");
  host.scrollTop = host.scrollHeight;
}

(async () => {
  try {
    const recent = await get("/events/recent");
    const after = recent.events?.at(-1)?.id || "";
    const events = new EventSource(`${API}/events?after=${encodeURIComponent(after)}`);
    events.onmessage = message => { try { renderLiveEvent(JSON.parse(message.data)); } catch (_) {} };
    events.onerror = () => { $("#connection-text").textContent = "Obnovuji živé kroky…"; };
    events.onopen = () => { $("#connection-text").textContent = "Lokální služby online"; };
  } catch (_) {}
})();

groupFor = agent => agent.group || "Core";
renderAgents = function() {
  $("#agent-tree").innerHTML = agentBranches.map(group => {
    const agents = state.agents.filter(agent => groupFor(agent) === group);
    const working = agents.filter(agent => agent.status === "working").length;
    const errors = agents.filter(agent => agent.status === "error").length;
    const hidden = collapsedBranches.has(group);
    return `<section class="agent-branch"><header><button class="branch-toggle" data-branch="${group}" type="button">${hidden ? "›" : "⌄"} ${group}</button><span class="branch-stats">${agents.length} · ${working} pracuje${errors ? ` · ${errors} chyba` : ""}</span><button data-add-agent-group="${group}" type="button">＋</button></header><div class="agent-children ${hidden ? "hidden" : ""}">${agents.map(agent => `<button class="agent-node ${state.activeAgentId === agent.id ? "selected" : ""}" data-agent="${escapeHtml(agent.id)}" type="button"><i class="${escapeHtml(agent.status || "ready")}"></i><span><b>${escapeHtml(agent.name)}</b><small>${escapeHtml(agent.current_step || agent.role || "Připraven")}</small><span class="agent-progress" style="--progress:${Number(agent.progress || 0)}%"></span></span><span>${escapeHtml(agent.status || "ready")}</span></button>`).join("") || '<div class="agent-node"><i class="disabled"></i><span><b>Prázdná větev</b><small>Přidej vlastního agenta.</small></span></div>'}</div></section>`;
  }).join("");
  $$('[data-agent]').forEach(button => button.onclick = () => { state.activeAgentId = button.dataset.agent; renderAgents(); renderAgentDetail(); });
  $$('[data-add-agent-group]').forEach(button => button.onclick = () => openAgentDialog(null, button.dataset.addAgentGroup));
  $$('[data-branch]').forEach(button => button.onclick = () => { const group = button.dataset.branch; collapsedBranches.has(group) ? collapsedBranches.delete(group) : collapsedBranches.add(group); localStorage.setItem("raven-collapsed-branches", JSON.stringify([...collapsedBranches])); renderAgents(); });
  renderAgentDetail();
};

renderAgentDetail = function() {
  const agent = state.agents.find(item => item.id === state.activeAgentId);
  if (!agent) { $("#agent-detail").innerHTML = '<div class="empty-detail">Vyber agenta ve stromu.</div>'; return; }
  $("#agent-detail").innerHTML = `<h2>${escapeHtml(agent.name)}</h2><p>${escapeHtml(agent.role || "")}</p><section class="detail-section"><small>Stav a průběh</small><p>${escapeHtml(agent.status || "ready")} · ${Number(agent.progress || 0)} % · ${escapeHtml(agent.current_step || "Připraven")}</p></section><section class="detail-section"><small>Model</small><p>${escapeHtml(agent.model || "automatic")}</p></section><section class="detail-section"><small>Nástroje</small><p>${escapeHtml((agent.tools || agent.permissions || []).join(", ") || "Žádné")}</p></section><section class="detail-section"><small>Cesta závislostí</small><p>${escapeHtml((agent.dependencies || []).join(" → ") || "Raven")}${agent.dependencies?.length ? ` → ${escapeHtml(agent.id)}` : ""}</p></section><section class="detail-section"><small>Aktuální úkol</small><p>${escapeHtml(agent.current_task || "Žádný")}</p></section><section class="detail-section"><small>Výsledek / chyba</small><p>${escapeHtml(agent.error || agent.last_result || "Zatím bez výsledku")}</p></section><footer><button id="edit-agent" type="button">Upravit</button>${agent.id !== "raven" ? '<button data-agent-action="start" type="button">Spustit</button><button data-agent-action="pause" type="button">Pozastavit</button><button data-agent-action="stop" type="button">Zastavit</button><button data-agent-action="retry" type="button">Opakovat</button><button id="delete-agent" type="button">Smazat</button>' : ""}</footer>`;
  $("#edit-agent").onclick = () => openAgentDialog(agent, groupFor(agent));
  $$('[data-agent-action]').forEach(button => button.onclick = async () => { await post("/agents/action", { agent_id: agent.id, action: button.dataset.agentAction }); const data = await get("/agents"); state.agents = data.agents || []; renderAgents(); });
  if (agent.id !== "raven") $("#delete-agent").onclick = async () => { if (await confirmAction(`Odstranit agenta ${agent.name}?`)) { const data = await post("/agents/delete", { id: agent.id }); state.agents = data.agents; state.activeAgentId = "raven"; renderAgents(); } };
};

const oldRenderSettings = renderSettings;
renderSettings = function() {
  oldRenderSettings();
  const providerSelect = $("#key-provider");
  if (providerSelect) providerSelect.innerHTML = state.providers.filter(provider => PROVIDER_KEY_PAGES[provider.id]).map(provider => `<option value="${escapeHtml(provider.id)}">${escapeHtml(provider.label)} · ${provider.configured ? "klíč uložen" : "bez klíče"}</option>`).join("");
  const first = $("#settings-form .settings-card");
  if (first) first.insertAdjacentHTML("beforeend", '<p class="free-only-note">Automatický router používá pouze bezplatné kvóty a lokální model. Placené API, automatické nákupy, Grok a xAI jsou zakázané.</p>');
  const actions = $("#settings-form .settings-actions");
  if (actions) actions.insertAdjacentHTML("beforebegin", '<section class="settings-card"><h2>Aktualizace</h2><p id="update-status">Zjišťuji stav nainstalované verze…</p><div class="settings-buttons"><button id="check-update" type="button">Zkontrolovat GitHub Releases</button><button id="install-update" type="button" disabled>Instalovat a restartovat</button></div></section>');
  if (actions) actions.insertAdjacentHTML("beforebegin", '<section class="settings-card"><h2>Synchronizace počítač / flashdisk / GitHub</h2><p id="sync-status">Porovnávám zdrojové soubory, Git a SHA-256…</p><div class="settings-buttons"><button id="select-portable-root" type="button">Vybrat přenosnou kopii</button><button id="check-sync" type="button">Porovnat včetně GitHubu</button></div><small>Raven zde nic automaticky nepřepisuje; při rozdílu vyžaduje volbu směru a potvrzení.</small></section>');
  const showUpdate = value => { const label=$("#update-status"), install=$("#install-update"); if(label)label.textContent=value?.message||"Stav aktualizace není dostupný."; if(install)install.disabled=value?.status!=="ready"; };
  desktop?.updater?.status().then(showUpdate).catch(error=>showUpdate({message:error.message}));
  $("#check-update")?.addEventListener("click", async()=>showUpdate(await desktop.updater.check()));
  $("#install-update")?.addEventListener("click", async()=>{if(await confirmAction("Nainstalovat staženou aktualizaci a restartovat Raven?"))await desktop.updater.install();});
  const showSync=value=>{const label=$("#sync-status");if(!label)return;const local=value?.local||{},portable=value?.portable||{};label.textContent=`${value?.recommendation||"Stav není dostupný."} · PC ${local.source_files||0} souborů/${String(local.manifest_sha256||"").slice(0,8)||"—"} · přenosná kopie ${portable.source_files||0} souborů/${String(portable.manifest_sha256||"").slice(0,8)||"—"}${value?.github_head?` · GitHub ${value.github_head.slice(0,8)}`:""}`;};
  get("/sync/status").then(showSync).catch(error=>showSync({recommendation:error.message}));
  $("#select-portable-root")?.addEventListener("click",async()=>{const selected=await desktop?.openFolder();if(selected)showSync(await post("/sync/settings",{portable_root:selected}));});
  $("#check-sync")?.addEventListener("click",async()=>showSync(await get("/sync/status?remote=1")));
};

function permissionMode() { return $("#composer-access")?.value || state.settings.permission_mode || "confirm"; }
async function allowEdit(message) {
  if (permissionMode() === "denied") { notify("Úpravy jsou v režimu Zakázáno vypnuté.", "error"); return false; }
  if (permissionMode() === "confirm") return confirmAction(message);
  return true;
}

function browserHost() { return document.querySelector(".native-browser-surface"); }
function syncBrowserBounds() {
  if (!desktop) return;
  const host = browserHost();
  const visible = Boolean(host && host.offsetParent && !$("#app-shell").classList.contains("workspace-hidden"));
  desktop.browser.setVisible(visible);
  if (!visible) return;
  const rect = host.getBoundingClientRect();
  desktop.browser.setBounds({ x: rect.x, y: rect.y, width: rect.width, height: rect.height });
}

function renderBrowser(container) {
  container.classList.add("browser-workspace");
  const active = browserState.tabs.find(tab => tab.id === browserState.activeTabId) || browserState.tabs[0];
  container.innerHTML = `<div class="browser-shell"><div class="browser-tabs">${browserState.tabs.map(tab => `<button class="browser-tab ${tab.id === browserState.activeTabId ? "active" : ""} ${tab.loading ? "loading" : ""}" data-browser-tab="${escapeHtml(tab.id)}" type="button"><i></i><span>${escapeHtml(tab.title || "Nová karta")}</span><b class="browser-tab-close" data-browser-close="${escapeHtml(tab.id)}">×</b></button>`).join("")}<button id="browser-new-tab" class="browser-new-tab" type="button" title="Nová karta">＋</button></div><form id="browser-navigation" class="browser-navigation"><button data-browser-action="back" type="button" ${active?.canGoBack ? "" : "disabled"}>←</button><button data-browser-action="forward" type="button" ${active?.canGoForward ? "" : "disabled"}>→</button><button data-browser-action="${active?.loading ? "stop" : "reload"}" type="button">${active?.loading ? "×" : "↻"}</button><input id="browser-url" aria-label="Webová adresa" value="${escapeHtml(active?.url || "https://github.com/")}"><button type="submit">Otevřít</button></form><div class="browser-extra-actions"><button id="browser-devtools" type="button">Konzole</button><button id="browser-capture" type="button">Snímek</button><button id="browser-diagnostics" type="button">Události</button></div><div class="native-browser-surface"></div></div>`;
  $$('[data-browser-tab]').forEach(button => button.onclick = event => { if (event.target.closest('[data-browser-close]')) return; desktop?.browser.select(button.dataset.browserTab); });
  $$('[data-browser-close]').forEach(button => button.onclick = event => { event.stopPropagation(); desktop?.browser.close(button.dataset.browserClose); });
  $("#browser-new-tab").onclick = () => desktop?.browser.create("https://github.com/");
  $$('[data-browser-action]').forEach(button => button.onclick = () => desktop?.browser.action(button.dataset.browserAction));
  $("#browser-navigation").onsubmit = event => { event.preventDefault(); desktop?.browser.navigate($("#browser-url").value); };
  $("#browser-devtools").onclick = () => desktop?.browser.action("devtools");
  $("#browser-capture").onclick = async () => { const result = await desktop?.browser.capture(); if (result?.path) notify(`Snímek uložen: ${result.path}`); };
  $("#browser-diagnostics").onclick = async () => { const result = await desktop?.browser.diagnostics(); state.activity.push(...(result?.events || []).slice(-30).map(item => ({ time: formatTime(item.at), text: `${item.type}: ${item.message || item.description || item.url}`, type: item.level >= 2 ? "error" : "info" }))); state.workspace = "output"; renderWorkspace(); };
  requestAnimationFrame(syncBrowserBounds);
}

function appendTerminalData(value) {
  const previous = terminalBuffers.get(value.id) || "";
  terminalBuffers.set(value.id, `${previous}${value.data || ""}`.slice(-200000));
  const output = document.querySelector(`.terminal-output[data-terminal-output="${CSS.escape(value.id)}"]`);
  if (output) { output.textContent = terminalBuffers.get(value.id); output.scrollTop = output.scrollHeight; }
}

async function refreshTerminals() {
  if (!desktop?.terminal) return;
  terminalState = await desktop.terminal.list();
  if (!activeTerminalId || !terminalState.terminals.some(item => item.id === activeTerminalId)) activeTerminalId = terminalState.terminals.at(-1)?.id || "";
}

async function renderTerminal(container = $("#workspace-content")) {
  desktop?.browser.setVisible(false);
  if (!desktop?.terminal) { container.innerHTML = '<p class="muted">Integrovaný terminál je dostupný v desktopové aplikaci.</p>'; return; }
  await refreshTerminals();
  if (!terminalState.terminals.length) {
    terminalState = await desktop.terminal.create({ cwd: state.currentPath && state.currentPath !== "::drives" ? state.currentPath : undefined });
    activeTerminalId = terminalState.terminals.at(-1)?.id || "";
  }
  const active = terminalState.terminals.find(item => item.id === activeTerminalId) || terminalState.terminals[0];
  activeTerminalId = active?.id || "";
  container.innerHTML = `<section class="terminal-shell"><nav class="terminal-tabs">${terminalState.terminals.map(item => `<button class="terminal-tab ${item.id === activeTerminalId ? "active" : ""}" data-terminal-tab="${escapeHtml(item.id)}" type="button">${escapeHtml(item.title)}${item.running ? "" : ` · ${item.exitCode}`}</button>`).join("")}<button id="terminal-new" type="button">＋</button></nav><pre class="terminal-output" data-terminal-output="${escapeHtml(activeTerminalId)}">${escapeHtml(terminalBuffers.get(activeTerminalId) || "")}</pre><form class="terminal-command"><input id="terminal-input" autocomplete="off" spellcheck="false" placeholder="PowerShell příkaz"><button type="submit">Spustit</button></form><div class="terminal-toolbar"><span>${escapeHtml(active?.cwd || "")}</span><button id="terminal-restart" type="button">Restartovat</button><button id="terminal-close" type="button">Zavřít</button></div></section>`;
  $$('[data-terminal-tab]').forEach(button => button.onclick = () => { activeTerminalId = button.dataset.terminalTab; renderTerminal(container); });
  $("#terminal-new").onclick = async () => { terminalState = await desktop.terminal.create({ cwd: active?.cwd }); activeTerminalId = terminalState.terminals.at(-1)?.id || ""; renderTerminal(container); };
  $("#terminal-restart").onclick = async () => { terminalState = await desktop.terminal.restart({ id: activeTerminalId, cwd: active?.cwd }); activeTerminalId = terminalState.terminals.at(-1)?.id || ""; renderTerminal(container); };
  $("#terminal-close").onclick = async () => { terminalState = await desktop.terminal.close({ id: activeTerminalId }); activeTerminalId = terminalState.terminals.at(-1)?.id || ""; renderTerminal(container); };
  container.querySelector('.terminal-command').onsubmit = async event => {
    event.preventDefault();
    const input = container.querySelector('#terminal-input');
    const command = input.value.trim();
    if (!command) return;
    if (!await allowEdit(`Spustit v terminálu: ${command}`)) return;
    appendTerminalData({ id: activeTerminalId, data: `PS> ${command}\r\n` });
    await desktop.terminal.write({ id: activeTerminalId, data: command, permissionMode: permissionMode(), confirmed: permissionMode() === "confirm" });
    input.value = "";
  };
}

async function openEditor(relative) {
  const data = await desktop.readFile(relative);
  if (!openFiles.includes(relative)) openFiles.push(relative);
  state.selectedFile = relative;
  const c = $("#workspace-content");
  c.classList.remove("browser-workspace");
  c.innerHTML = `<div class="editor-tabs">${openFiles.map(file => `<button class="editor-tab ${file === relative ? "active" : ""}" data-editor-file="${escapeHtml(file)}">${escapeHtml(file.split(/[\\/]/).at(-1))}<span class="unsaved-dot hidden">●</span></button>`).join("")}</div><div class="editor-actions"><button id="back-files" type="button">← Soubory</button><span>${escapeHtml(relative)}</span><button id="editor-diff" type="button">Git diff</button><button id="editor-save" type="button">Uložit</button></div><div id="monaco-host" class="monaco-host"></div>`;
  $$('[data-editor-file]').forEach(button => button.onclick = () => openEditor(button.dataset.editorFile));
  $("#back-files").onclick = () => renderFiles(state.currentPath);
  $("#editor-diff").onclick = () => { state.workspace = "git"; renderWorkspace(); };
  const create = () => {
    editor?.dispose(); editorModel?.dispose();
    editorModel = monaco.editor.createModel(data.content, data.language === "js" ? "javascript" : data.language, monaco.Uri.parse(`file:///${relative}`));
    editor = monaco.editor.create($("#monaco-host"), { model: editorModel, theme: "vs-dark", automaticLayout: true, fontSize: 13, minimap: { enabled: true }, scrollBeyondLastLine: false });
    editor.onDidChangeModelContent(() => document.querySelector('.editor-tab.active .unsaved-dot')?.classList.remove('hidden'));
  };
  if (window.monaco) create(); else if (window.require) { require.config({ paths: { vs: "vendor/monaco/vs" } }); require(["vs/editor/editor.main"], create); }
  $("#editor-save").onclick = async () => {
    if (!editor || !await allowEdit(`Uložit změny do ${relative}?`)) return;
    await desktop.writeFile({ path: relative, content: editor.getValue(), permissionMode: permissionMode(), confirmed: permissionMode() === "confirm" });
    document.querySelector('.editor-tab.active .unsaved-dot')?.classList.add('hidden');
    notify(`Soubor ${relative} byl uložen.`); refreshChangeCard();
  };
}

renderFiles = async function(path = state.currentPath || "::drives", remember = true, searchQuery = "") {
  desktop?.browser.setVisible(false);
  const c = $("#workspace-content"); c.classList.remove("browser-workspace");
  if (!desktop) { c.innerHTML = '<p class="muted">Soubory jsou dostupné v desktopové aplikaci.</p>'; return; }
  try {
    const data = searchQuery ? await desktop.searchFiles({ root: path, query: searchQuery }) : await desktop.listFiles({ path: path || "::drives" });
    if (!searchQuery) state.currentPath = data.path || "::drives";
    if (remember && fileNavigation.history[fileNavigation.index] !== state.currentPath) { fileNavigation.history = fileNavigation.history.slice(0, fileNavigation.index + 1); fileNavigation.history.push(state.currentPath); fileNavigation.index = fileNavigation.history.length - 1; }
    const entries = data.entries || data.results || [];
    c.innerHTML = `<div class="file-shell"><form id="file-navigation" class="workspace-toolbar file-navigation"><button id="file-back" type="button" ${fileNavigation.index <= 0 ? "disabled" : ""}>←</button><button id="file-forward" type="button" ${fileNavigation.index >= fileNavigation.history.length - 1 ? "disabled" : ""}>→</button><button id="file-up" type="button" ${searchQuery || data.parent == null ? "disabled" : ""}>↑</button><button id="file-computer" type="button">PC</button><input id="file-path" value="${escapeHtml(searchQuery ? data.root : data.displayPath || data.path)}"><button type="submit">Otevřít</button></form><div class="file-actions"><button id="file-new" type="button" ${state.currentPath === "::drives" ? "disabled" : ""}>＋ Soubor</button><button id="folder-new" type="button" ${state.currentPath === "::drives" ? "disabled" : ""}>＋ Složka</button><button id="file-search" type="button" ${state.currentPath === "::drives" ? "disabled" : ""}>Hledat</button>${searchQuery ? '<button id="file-search-clear" type="button">Zrušit hledání</button>' : ""}</div><div class="file-list">${entries.map(item => `<div class="workspace-entry ${selectedFilePath === item.path ? "selected" : ""}" data-file-kind="${item.kind}" data-file-path="${escapeHtml(item.path)}">${item.drive ? "▣" : item.kind === "directory" ? "▣" : "·"} ${escapeHtml(item.name)}<small>${item.kind === "file" ? `${item.size || 0} B` : item.drive ? "Místní disk" : item.path}</small>${item.drive ? "" : `<div class="entry-actions"><button data-file-rename="${escapeHtml(item.path)}" type="button">Přejmenovat</button><button data-file-delete="${escapeHtml(item.path)}" type="button">Do koše</button></div>`}</div>`).join("") || '<p class="muted">Nic nenalezeno.</p>'}</div></div>`;
    $("#file-back").onclick = () => { if (fileNavigation.index > 0) renderFiles(fileNavigation.history[--fileNavigation.index], false); };
    $("#file-forward").onclick = () => { if (fileNavigation.index < fileNavigation.history.length - 1) renderFiles(fileNavigation.history[++fileNavigation.index], false); };
    $("#file-up").onclick = () => renderFiles(data.parent || "::drives");
    $("#file-computer").onclick = () => renderFiles("::drives");
    $("#file-navigation").onsubmit = event => { event.preventDefault(); const value = $("#file-path").value.trim(); renderFiles(value === "Tento počítač" ? "::drives" : value); };
    $("#file-new").onclick = async () => { const name = prompt("Název nového souboru:"); if (!name || !await allowEdit(`Vytvořit soubor ${name}?`)) return; await desktop.createFile({ directory: state.currentPath, name, kind: "file", permissionMode: permissionMode(), confirmed: permissionMode() === "confirm" }); renderFiles(state.currentPath, false); };
    $("#folder-new").onclick = async () => { const name = prompt("Název nové složky:"); if (!name || !await allowEdit(`Vytvořit složku ${name}?`)) return; await desktop.createFile({ directory: state.currentPath, name, kind: "directory", permissionMode: permissionMode(), confirmed: permissionMode() === "confirm" }); renderFiles(state.currentPath, false); };
    $("#file-search").onclick = () => { const query = prompt("Hledat název souboru nebo složky:"); if (query) renderFiles(state.currentPath, false, query); };
    $("#file-search-clear")?.addEventListener("click", () => renderFiles(state.currentPath, false));
    $$('[data-file-path]').forEach(item => item.onclick = event => { if (event.target.closest('.entry-actions')) return; selectedFilePath = item.dataset.filePath; item.dataset.fileKind === "directory" ? renderFiles(item.dataset.filePath) : openEditor(item.dataset.filePath); });
    $$('[data-file-rename]').forEach(button => button.onclick = async event => { event.stopPropagation(); const name = prompt("Nový název:", button.dataset.fileRename.split(/[\\/]/).at(-1)); if (!name || !await allowEdit(`Přejmenovat ${button.dataset.fileRename}?`)) return; await desktop.renameFile({ path: button.dataset.fileRename, name, permissionMode: permissionMode(), confirmed: permissionMode() === "confirm" }); renderFiles(state.currentPath, false); });
    $$('[data-file-delete]').forEach(button => button.onclick = async event => { event.stopPropagation(); if (!await allowEdit(`Obnovitelně odstranit ${button.dataset.fileDelete}?`)) return; const result = await desktop.deleteFile({ path: button.dataset.fileDelete, permissionMode: permissionMode(), confirmed: permissionMode() === "confirm" }); notify(`Přesunuto do koše Ravenu: ${result.trashPath}`); renderFiles(state.currentPath, false); });
  } catch (error) { c.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`; }
};

renderGit = async function() {
  const c = $("#workspace-content");
  if (!desktop?.gitChanges) { c.innerHTML = '<p class="muted">Rozšířený Git je dostupný v desktopové aplikaci.</p>'; return; }
  try {
    if (selectedSnapshotName) {
      const snapshot = await desktop.gitSnapshotFiles({ name: selectedSnapshotName });
      c.innerHTML = `<div class="workspace-toolbar"><button id="snapshot-back" type="button">← Git</button><b>${escapeHtml(snapshot.name)}</b></div><div class="snapshot-files">${snapshot.files.map(file => `<div class="workspace-entry"><span>${escapeHtml(file.status)} · ${escapeHtml(file.path)}</span>${file.status!=="same"?`<button data-snapshot-restore="${escapeHtml(file.path)}" type="button">Obnovit soubor</button>`:""}</div>`).join("")||'<p class="muted">Snapshot je prázdný.</p>'}</div>`;
      $("#snapshot-back").onclick=()=>{selectedSnapshotName="";renderGit();};
      $$('[data-snapshot-restore]').forEach(button=>button.onclick=async()=>{if(!await allowEdit(`Obnovit ${button.dataset.snapshotRestore} ze snapshotu ${selectedSnapshotName}?`))return;const result=await desktop.gitSnapshotRestore({name:selectedSnapshotName,path:button.dataset.snapshotRestore,permissionMode:permissionMode(),confirmed:permissionMode()==="confirm"});notify(`Obnoveno · bezpečnostní snapshot ${result.safetySnapshot}`);renderGit();});
      return;
    }
    if (state.selectedFile) {
      const diff = await desktop.gitDiff(state.selectedFile);
      c.innerHTML = `<div class="workspace-toolbar"><button id="git-status-back" type="button">← Stav</button><span>${escapeHtml(state.selectedFile)}</span></div><pre class="workspace-code">${escapeHtml(diff.content)}</pre>`;
      $("#git-status-back").onclick = () => { state.selectedFile = ""; renderGit(); };
      return;
    }
    const [changes, snapshots] = await Promise.all([desktop.gitChanges(), desktop.gitSnapshots()]);
    c.innerHTML = `<div class="git-actions"><b>${escapeHtml(changes.branch || "bez větve")} · ${escapeHtml(changes.head || "")}</b><button id="git-snapshot-new" type="button">Vytvořit snapshot</button><button id="git-commit" type="button">Commit</button></div><section><h3>Staged</h3>${changes.rows.filter(row => row.index !== " " && row.index !== "?").map(row => `<div class="workspace-entry"><span data-git-open="${escapeHtml(row.path)}">${escapeHtml(row.index)} ${escapeHtml(row.path)}</span><button data-git-unstage="${escapeHtml(row.path)}" type="button">Unstage</button></div>`).join("") || '<p class="muted">Žádné staged změny.</p>'}</section><section><h3>Pracovní změny</h3>${changes.rows.filter(row => row.worktree !== " " || row.index === "?").map(row => `<div class="workspace-entry"><span data-git-open="${escapeHtml(row.path)}">${escapeHtml(row.worktree === " " ? row.index : row.worktree)} ${escapeHtml(row.path)}</span><span><button data-git-stage="${escapeHtml(row.path)}" type="button">Stage</button>${row.index!=="?"?`<button data-git-discard="${escapeHtml(row.path)}" type="button">Vrátit</button>`:""}</span></div>`).join("") || '<p class="muted">Žádné pracovní změny.</p>'}</section><details><summary>Snapshoty (${snapshots.snapshots.length})</summary>${snapshots.snapshots.map(name => `<button class="workspace-entry" data-snapshot-open="${escapeHtml(name)}" type="button">${escapeHtml(name)}</button>`).join("") || '<p class="muted">Žádné snapshoty.</p>'}</details>`;
    $$('[data-git-open]').forEach(node => node.onclick = () => { state.selectedFile = node.dataset.gitOpen; renderGit(); });
    $$('[data-git-stage]').forEach(button => button.onclick = async () => { if (!await allowEdit(`Přidat ${button.dataset.gitStage} do stage?`)) return; await desktop.gitStage({ path: button.dataset.gitStage, permissionMode: permissionMode(), confirmed: permissionMode() === "confirm" }); renderGit(); });
    $$('[data-git-unstage]').forEach(button => button.onclick = async () => { if (!await allowEdit(`Odebrat ${button.dataset.gitUnstage} ze stage?`)) return; await desktop.gitUnstage({ path: button.dataset.gitUnstage, permissionMode: permissionMode(), confirmed: permissionMode() === "confirm" }); renderGit(); });
    $$('[data-git-discard]').forEach(button => button.onclick = async () => { if (!await allowEdit(`Vrátit pracovní změny v ${button.dataset.gitDiscard}? Předem vznikne bezpečnostní snapshot.`)) return; const result=await desktop.gitDiscard({path:button.dataset.gitDiscard,permissionMode:permissionMode(),confirmed:permissionMode()==="confirm"});notify(`Změna vrácena · snapshot ${result.safetySnapshot}`);renderGit(); });
    $$('[data-snapshot-open]').forEach(button=>button.onclick=()=>{selectedSnapshotName=button.dataset.snapshotOpen;renderGit();});
    $("#git-snapshot-new").onclick = async () => { const label = prompt("Název snapshotu:", "ruční-bod"); if (!label || !await allowEdit("Vytvořit snapshot projektu?")) return; const result = await desktop.createSnapshot(label); notify(`Snapshot vytvořen: ${result.name}`); renderGit(); };
    $("#git-commit").onclick = async () => { const message = prompt("Zpráva commitu:"); if (!message || !await allowEdit(`Vytvořit commit „${message}“?`)) return; const result = await desktop.gitCommit({ message, permissionMode: permissionMode(), confirmed: permissionMode() === "confirm" }); notify(result.output || "Commit vytvořen"); renderGit(); };
  } catch (error) {
    c.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`;
  }
};

const oldRenderWorkspace = renderWorkspace;
renderWorkspace = async function() {
  const c = $("#workspace-content");
  c.classList.remove("browser-workspace");
  $("#workspace-title").textContent = ({ browser: "Prohlížeč", files: "Soubory", terminal: "Terminál", output: "Výstup", git: "Git změny", logs: "Logy", memory: "Paměť", artifacts: "Artefakty" })[state.workspace] || "Pracovna";
  $$('[data-workspace]').forEach(button => button.classList.toggle("active", button.dataset.workspace === state.workspace));
  desktop?.browser.setVisible(false);
  if (state.workspace === "browser") { if (desktop) renderBrowser(c); else oldRenderWorkspace(); return; }
  if (state.workspace === "files") { await renderFiles(); return; }
  if (state.workspace === "terminal") { await renderTerminal(c); return; }
  if (state.workspace === "artifacts") { try { const rootPath = await desktop.rootPath(); const data = await desktop.listFiles({ path: `${rootPath}\\runtime\\artifacts` }); c.innerHTML = data.entries.map(item => `<div class="workspace-entry">${escapeHtml(item.name)}<small>${item.size || 0} B</small></div>`).join("") || '<p class="muted">Zatím nejsou žádné artefakty.</p>'; } catch { c.innerHTML = '<p class="muted">Zatím nejsou žádné artefakty.</p>'; } return; }
  await oldRenderWorkspace();
};

renderWebPage = function() {
  const c = $("#web-page-content");
  if (!desktop) { c.innerHTML = '<p class="browser-note">Skutečný prohlížeč je dostupný v desktopové aplikaci.</p>'; return; }
  renderBrowser(c);
};

const oldShowView = showView;
showView = function(name) {
  desktop?.browser.setVisible(false);
  oldShowView(name);
  $("#task-progress").classList.add("hidden");
  if (name !== "web" && state.workspace === "browser" && !$("#app-shell").classList.contains("workspace-hidden")) renderWorkspace();
  requestAnimationFrame(syncBrowserBounds);
};

async function refreshChangeCard() {
  if (!desktop) return;
  try {
    const summary = await desktop.gitSummary(); const card = $("#change-card");
    const signature = JSON.stringify([summary.count, summary.added, summary.removed, summary.entries]);
    if (!changeBaseline) { changeBaseline = summary; lastChangeSignature = signature; card.classList.add("hidden"); return; }
    const baselineSignature = JSON.stringify([changeBaseline.count, changeBaseline.added, changeBaseline.removed, changeBaseline.entries]);
    if (signature === baselineSignature || changeCardDismissed) { card.classList.add("hidden"); return; }
    if (signature !== lastChangeSignature) { lastChangeSignature = signature; clearTimeout(changeCardHideTimer); changeCardHideTimer = null; }
    const added = Math.abs(Number(summary.added || 0) - Number(changeBaseline.added || 0));
    const removed = Math.abs(Number(summary.removed || 0) - Number(changeBaseline.removed || 0));
    const previous = new Set(changeBaseline.entries || []);
    const changedEntries = (summary.entries || []).filter(entry => !previous.has(entry));
    const visibleEntries = changedEntries.length ? changedEntries : summary.entries || [];
    const changedCount = Math.max(changedEntries.length, 1);
    card.classList.remove("hidden");
    card.innerHTML = `<header><b>Aktuální úkol změnil ${changedCount} ${changedCount === 1 ? "soubor" : "souborů"}</b><span class="counts"><span class="plus">+${added}</span> <span class="minus">-${removed}</span></span><button id="change-open-diff" type="button">Změny</button><button id="change-card-close" class="change-card-close" type="button" title="Skrýt">×</button></header><details><summary>Seznam souborů</summary><div class="change-card-files">${visibleEntries.map(escapeHtml).join("<br>")}</div></details>`;
    $("#change-open-diff").onclick = () => { state.workspace = "git"; $("#app-shell").classList.remove("workspace-hidden"); renderWorkspace(); };
    $("#change-card-close").onclick = () => { changeCardDismissed = true; clearTimeout(changeCardHideTimer); changeCardHideTimer = null; card.classList.add("hidden"); };
    if (!state.running && !changeCardHideTimer) changeCardHideTimer = setTimeout(() => { card.classList.add("hidden"); changeCardHideTimer = null; }, 7000);
  } catch (_) {}
}

async function beginChangeTracking() {
  if (!desktop) return;
  try {
    changeBaseline = await desktop.gitSummary();
    lastChangeSignature = JSON.stringify([changeBaseline.count, changeBaseline.added, changeBaseline.removed, changeBaseline.entries]);
    changeCardDismissed = false;
    clearTimeout(changeCardHideTimer);
    changeCardHideTimer = null;
    $("#change-card").classList.add("hidden");
  } catch (_) {}
}

const sendMessageWithChangeTracking = sendMessage;
sendMessage = async function(text) {
  await beginChangeTracking();
  try { return await sendMessageWithChangeTracking(text); }
  finally {
    await refreshChangeCard();
    if (!$("#change-card").classList.contains("hidden") && !changeCardHideTimer) changeCardHideTimer = setTimeout(() => { $("#change-card").classList.add("hidden"); changeCardHideTimer = null; }, 7000);
  }
};

function setupWorkspaceResize() {
  const shell = $("#app-shell"), handle = $("#workspace-resizer");
  const saved = Math.max(320, Math.min(900, Number(localStorage.getItem("raven-workspace-width")) || 430));
  shell.style.setProperty("--workspace-w", `${saved}px`);
  handle.addEventListener("pointerdown", event => {
    event.preventDefault(); handle.setPointerCapture(event.pointerId); handle.classList.add("dragging");
    const move = moveEvent => { const width = Math.max(320, Math.min(window.innerWidth * .68, window.innerWidth - moveEvent.clientX)); shell.style.setProperty("--workspace-w", `${width}px`); localStorage.setItem("raven-workspace-width", String(Math.round(width))); syncBrowserBounds(); };
    const up = () => { handle.classList.remove("dragging"); handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", up); };
    handle.addEventListener("pointermove", move); handle.addEventListener("pointerup", up);
  });
}

if (desktop) {
  desktop.onBrowserState(value => { browserState = value; const host = browserHost(); if (host) renderBrowser(host.closest(".workspace-content, .browser-page")); });
  desktop.browser.list().then(value => { browserState = value; renderWorkspace(); });
  desktop.onTerminalData?.(appendTerminalData);
  desktop.onTerminalExit?.(value => { const terminal = terminalState.terminals.find(item => item.id === value.id); if (terminal) { terminal.running = false; terminal.exitCode = value.exitCode; } const host = document.querySelector('.terminal-shell')?.closest('.workspace-content'); if (host) renderTerminal(host); });
  desktop.updater?.onStatus(value => { const label=$("#update-status"), install=$("#install-update"); if(label)label.textContent=value?.message||"Stav aktualizace není dostupný."; if(install)install.disabled=value?.status!=="ready"; });
  new ResizeObserver(syncBrowserBounds).observe($("#app-shell"));
}
setupWorkspaceResize();
$("#detach-workspace").onclick = () => desktop?.openWorkspaceWindow(state.workspace);
$("#split-workspace").onclick = async () => {
  const split = $("#workspace-split"), secondary = $("#workspace-secondary");
  const enabled = !split.classList.contains("split-active");
  split.classList.toggle("split-active", enabled);
  secondary.classList.toggle("hidden", !enabled);
  if (enabled) {
    if (state.workspace === "terminal") secondary.innerHTML = state.activity.map(item => `<div class="output-entry"><time>${escapeHtml(item.time)}</time>${escapeHtml(item.text)}</div>`).join("") || '<p class="muted">Žádné výstupy.</p>';
    else await renderTerminal(secondary);
  } else secondary.innerHTML = "";
  localStorage.setItem("raven-workspace-split", enabled ? "1" : "0");
};
window.addEventListener("resize", syncBrowserBounds);
window.addEventListener("beforeunload", () => desktop?.browser.setVisible(false));
setInterval(refreshChangeCard, 4000);
refreshChangeCard();

const display = new URLSearchParams(location.search).get("display") || "";
if (display.startsWith("workspace:")) { state.workspace = display.split(":", 2)[1] || "output"; renderWorkspace(); }

function closeAppMenus() { $$(".app-menu[open]").forEach(menu => menu.removeAttribute("open")); }
function selectAdjacentChat(direction) {
  if (!state.chats.length) return;
  const index = Math.max(0, state.chats.findIndex(chat => chat.id === state.activeChatId));
  const next = state.chats[(index + direction + state.chats.length) % state.chats.length];
  if (next) { state.activeChatId = next.id; renderChats(); renderMessages(); showView("chat"); }
}
async function runMenuAction(action) {
  closeAppMenus();
  const focused = document.activeElement;
  if (action === "new-window") return desktop?.newWindow();
  if (action === "new-chat") return createChat();
  if (action === "open-folder") { if (!desktop) return; const folder = await desktop.openFolder(); if (folder) { state.workspace = "files"; $("#app-shell").classList.remove("workspace-hidden"); await renderFiles(folder); } return; }
  if (action === "close-chat") { if (!state.activeChatId) return; const payload = await post("/chats/delete", { id: state.activeChatId }); state.chats = payload.chats || []; state.activeChatId = payload.active_chat_id || state.chats.at(-1)?.id || ""; if (!state.activeChatId) await createChat(); else { renderChats(); renderMessages(); } return; }
  if (action === "quit") return desktop ? desktop.close() : window.close();
  if (["undo","redo","cut","copy","paste","delete","select-all"].includes(action)) { const commands = { undo:"undo",redo:"redo",cut:"cut",copy:"copy",paste:"paste",delete:"delete", "select-all":"selectAll" }; focused?.focus(); document.execCommand(commands[action]); return; }
  if (action === "settings") return showView("settings");
  if (action === "sidebar") return $("#app-shell").classList.toggle("sidebar-collapsed");
  if (action === "workspace") return $("#app-shell").classList.toggle("workspace-hidden");
  if (action === "files") { state.workspace="files"; $("#app-shell").classList.remove("workspace-hidden"); return renderWorkspace(); }
  if (action === "terminal") { state.workspace="terminal"; $("#app-shell").classList.remove("workspace-hidden"); return renderWorkspace(); }
  if (action === "browser") { state.workspace="browser"; $("#app-shell").classList.remove("workspace-hidden"); return renderWorkspace(); }
  if (action === "find") { const query=prompt("Najít na aktuální stránce:"); if (query) window.find(query,false,false,true); return; }
  if (action === "previous-chat") return selectAdjacentChat(-1);
  if (action === "next-chat") return selectAdjacentChat(1);
  if (action === "back") return state.workspace === "files" && fileNavigation.index > 0 ? renderFiles(fileNavigation.history[--fileNavigation.index], false) : desktop?.browser.action("back");
  if (action === "forward") return state.workspace === "files" && fileNavigation.index < fileNavigation.history.length - 1 ? renderFiles(fileNavigation.history[++fileNavigation.index], false) : desktop?.browser.action("forward");
  if (action === "zoom-in") return window.setRavenZoom ? window.setRavenZoom(Number(state.settings.ui_zoom_percent||100)+10) : desktop?.zoom("in");
  if (action === "zoom-out") return window.setRavenZoom ? window.setRavenZoom(Number(state.settings.ui_zoom_percent||100)-10) : desktop?.zoom("out");
  if (action === "zoom-reset") return window.setRavenZoom ? window.setRavenZoom(100) : desktop?.zoom("reset");
  if (action === "fullscreen") return desktop ? desktop.toggleFullscreen() : document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen();
}
$$('[data-menu-action]').forEach(button => button.onclick = () => runMenuAction(button.dataset.menuAction));
$$('.app-menu').forEach(menu => menu.addEventListener('toggle', () => { if (menu.open) $$('.app-menu').filter(other => other !== menu).forEach(other => other.removeAttribute('open')); }));
document.addEventListener('pointerdown', event => { if (!event.target.closest('.app-menu')) closeAppMenus(); });
document.addEventListener('keydown', event => {
  const ctrl = event.ctrlKey || event.metaKey; let action = "";
  if (event.key === "F11") action="fullscreen";
  else if (ctrl && event.key.toLowerCase()==="n") action="new-chat";
  else if (ctrl && event.key.toLowerCase()==="o") action="open-folder";
  else if (ctrl && event.key.toLowerCase()==="q") action="quit";
  else if (ctrl && event.key===",") action="settings";
  else if (ctrl && event.key.toLowerCase()==="b" && !event.altKey) action="sidebar";
  else if (ctrl && event.key.toLowerCase()==="j") action="workspace";
  else if (ctrl && event.shiftKey && event.key.toLowerCase()==="e") action="files";
  else if (ctrl && event.key==="`") action="terminal";
  else if (ctrl && event.altKey && event.key.toLowerCase()==="b") action="browser";
  else if (ctrl && event.key==="0") action="zoom-reset";
  else if (ctrl && (event.key==="+" || event.key==="=")) action="zoom-in";
  else if (ctrl && event.key==="-") action="zoom-out";
  if (action) { event.preventDefault(); runMenuAction(action); }
});

function paletteCommands() {
  const fixed = [
    ["Nový chat", "Soubor", () => createChat()],
    ["Otevřít složku", "Soubory", () => runMenuAction("open-folder")],
    ["Hlavní chat", "Okno", () => showView("chat")],
    ["Telemetrie", "Okno", () => showView("telemetry")],
    ["Procesy", "Okno", () => showView("processes")],
    ["Agenti", "Okno", () => showView("agents")],
    ["Integrovaný terminál", "Nástroj", () => runMenuAction("terminal")],
    ["Soubory", "Nástroj", () => runMenuAction("files")],
    ["Git změny", "Nástroj", () => { state.workspace = "git"; $("#app-shell").classList.remove("workspace-hidden"); renderWorkspace(); }],
    ["Prohlížeč", "Nástroj", () => runMenuAction("browser")],
    ["Nastavení", "Raven", () => showView("settings")],
    ["Ukončit Raven", "Raven", () => runMenuAction("quit")]
  ].map(([label, category, run]) => ({ label, category, run }));
  const chats = state.chats.map(chat => ({ label: chat.title || "Nový chat", category: "Chat", run: () => { state.activeChatId = chat.id; renderChats(); renderMessages(); showView("chat"); } }));
  const projects = state.projects.map(project => ({ label: project.name, category: "Projekt", run: async () => { const data = await post("/projects", { ...project, activate: true }); state.projects = data.projects; state.activeProjectId = data.active_project_id; renderProjects(); } }));
  return [...fixed, ...projects, ...chats];
}

function renderCommandPalette(query = "") {
  const normalized = query.trim().toLocaleLowerCase("cs");
  const commands = paletteCommands().filter(item => !normalized || `${item.label} ${item.category}`.toLocaleLowerCase("cs").includes(normalized)).slice(0, 40);
  $("#command-palette-results").innerHTML = commands.map((item, index) => `<button class="palette-item ${index === 0 ? "active" : ""}" data-palette-index="${index}" type="button"><span>${escapeHtml(item.label)}</span><small>${escapeHtml(item.category)}</small></button>`).join("") || '<p class="muted">Žádný příkaz nebyl nalezen.</p>';
  $$('[data-palette-index]').forEach(button => button.onclick = async () => { $("#command-palette-dialog").close(); await commands[Number(button.dataset.paletteIndex)].run(); });
  $("#command-palette-results").dataset.commands = String(commands.length);
  $("#command-palette-results")._commands = commands;
}

function openCommandPalette() {
  const dialog = $("#command-palette-dialog"), input = $("#command-palette-search");
  input.value = "";
  renderCommandPalette();
  dialog.showModal();
  requestAnimationFrame(() => input.focus());
}

$("#open-command-palette").onclick = openCommandPalette;
$("#command-palette-search").oninput = event => renderCommandPalette(event.target.value);
$("#command-palette-search").onkeydown = async event => {
  if (event.key !== "Enter") return;
  event.preventDefault();
  const command = $("#command-palette-results")._commands?.[0];
  if (command) { $("#command-palette-dialog").close(); await command.run(); }
};
document.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.shiftKey && event.key.toLowerCase() === "p") { event.preventDefault(); event.stopImmediatePropagation(); openCommandPalette(); }
}, true);

function updateStatusStrip() {
  const permission = permissionMode();
  const permissionNode = $("#status-permission");
  if (permissionNode) {
    permissionNode.textContent = ({ full: "Plný přístup", confirm: "Na potvrzení", denied: "Zakázáno" })[permission] || permission;
    permissionNode.className = `permission-${permission}`;
  }
  const provider = state.providers.find(item => item.id === (state.settings.ai_provider || "automatic"));
  if ($("#status-provider")) $("#status-provider").textContent = `Model: ${provider?.label || state.settings.ai_provider || "automaticky"}`;
  const active = state.agents.find(item => item.status === "working") || state.agents.find(item => item.id === state.activeAgentId);
  if ($("#status-agent") && !state.running) $("#status-agent").textContent = `${active?.name || "Raven"} · ${active?.status || "připraven"}`;
  if ($("#status-services")) $("#status-services").textContent = $("#connection-text")?.textContent || "Služby: neznámé";
}

setInterval(updateStatusStrip, 1500);
updateStatusStrip();
if (localStorage.getItem("raven-workspace-split") === "1") requestAnimationFrame(() => $("#split-workspace")?.click());

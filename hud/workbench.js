"use strict";

const desktop = window.ravenDesktop || null;
let browserState = { activeTabId: "", tabs: [] };
let editor = null;
let editorModel = null;
const openFiles = [];
const fileNavigation = { history: [], index: -1 };
const agentBranches = ["Core", "Planning", "Research", "Browser", "Coding", "Testing", "Files", "Memory", "Security", "System"];
const collapsedBranches = new Set(JSON.parse(localStorage.getItem("raven-collapsed-branches") || "[]"));
let changeBaseline = null;
let lastChangeSignature = "";
let changeCardDismissed = true;
let changeCardHideTimer = null;
let liveWorkHideTimer = null;

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
    create_snapshot: desktop.createSnapshot
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
  $$('[data-agent-action]').forEach(button => button.onclick = async () => { const data = await post("/agents/action", { agent_id: agent.id, action: button.dataset.agentAction }); state.agents = data.agents; renderAgents(); });
  if (agent.id !== "raven") $("#delete-agent").onclick = async () => { if (await confirmAction(`Odstranit agenta ${agent.name}?`)) { const data = await post("/agents/delete", { id: agent.id }); state.agents = data.agents; state.activeAgentId = "raven"; renderAgents(); } };
};

const oldRenderSettings = renderSettings;
renderSettings = function() {
  oldRenderSettings();
  const providerSelect = $("#key-provider");
  if (providerSelect) providerSelect.innerHTML = state.providers.filter(provider => !["automatic", "local"].includes(provider.id)).map(provider => `<option value="${escapeHtml(provider.id)}">${escapeHtml(provider.label)} · ${provider.configured ? "nastaven" : "bez klíče"}</option>`).join("");
  const first = $("#settings-form .settings-card");
  if (first) first.insertAdjacentHTML("beforeend", '<p class="free-only-note">Pouze bezplatné režimy. Grok, xAI a placené přepnutí jsou trvale zakázané.</p>');
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
  container.innerHTML = `<div class="browser-shell"><div class="browser-tabs">${browserState.tabs.map(tab => `<button class="browser-tab ${tab.id === browserState.activeTabId ? "active" : ""} ${tab.loading ? "loading" : ""}" data-browser-tab="${escapeHtml(tab.id)}" type="button"><i></i><span>${escapeHtml(tab.title || "Nová karta")}</span><b class="browser-tab-close" data-browser-close="${escapeHtml(tab.id)}">×</b></button>`).join("")}<button id="browser-new-tab" class="browser-new-tab" type="button" title="Nová karta">＋</button></div><form id="browser-navigation" class="browser-navigation"><button data-browser-action="back" type="button" ${active?.canGoBack ? "" : "disabled"}>←</button><button data-browser-action="forward" type="button" ${active?.canGoForward ? "" : "disabled"}>→</button><button data-browser-action="${active?.loading ? "stop" : "reload"}" type="button">${active?.loading ? "×" : "↻"}</button><input id="browser-url" aria-label="Webová adresa" value="${escapeHtml(active?.url || "https://github.com/")}"><button type="submit">Otevřít</button></form><div class="native-browser-surface"></div></div>`;
  $$('[data-browser-tab]').forEach(button => button.onclick = event => { if (event.target.closest('[data-browser-close]')) return; desktop?.browser.select(button.dataset.browserTab); });
  $$('[data-browser-close]').forEach(button => button.onclick = event => { event.stopPropagation(); desktop?.browser.close(button.dataset.browserClose); });
  $("#browser-new-tab").onclick = () => desktop?.browser.create("https://github.com/");
  $$('[data-browser-action]').forEach(button => button.onclick = () => desktop?.browser.action(button.dataset.browserAction));
  $("#browser-navigation").onsubmit = event => { event.preventDefault(); desktop?.browser.navigate($("#browser-url").value); };
  requestAnimationFrame(syncBrowserBounds);
}

async function openEditor(relative) {
  const data = await desktop.readFile(relative);
  if (!openFiles.includes(relative)) openFiles.push(relative);
  state.selectedFile = relative;
  const c = $("#workspace-content");
  c.classList.remove("browser-workspace");
  c.innerHTML = `<div class="editor-tabs">${openFiles.map(file => `<button class="editor-tab ${file === relative ? "active" : ""}" data-editor-file="${escapeHtml(file)}">${escapeHtml(file.split("/").at(-1))}</button>`).join("")}</div><div class="editor-actions"><button id="back-files" type="button">← Soubory</button><span>${escapeHtml(relative)}</span><button id="editor-diff" type="button">Git diff</button><button id="editor-save" type="button">Uložit</button></div><div id="monaco-host" class="monaco-host"></div>`;
  $$('[data-editor-file]').forEach(button => button.onclick = () => openEditor(button.dataset.editorFile));
  $("#back-files").onclick = () => renderFiles(state.currentPath);
  $("#editor-diff").onclick = () => { state.workspace = "git"; renderWorkspace(); };
  const create = () => {
    editor?.dispose(); editorModel?.dispose();
    editorModel = monaco.editor.createModel(data.content, data.language === "js" ? "javascript" : data.language, monaco.Uri.parse(`file:///${relative}`));
    editor = monaco.editor.create($("#monaco-host"), { model: editorModel, theme: "vs-dark", automaticLayout: true, fontSize: 12, minimap: { enabled: true }, scrollBeyondLastLine: false });
  };
  if (window.monaco) create(); else if (window.require) { require.config({ paths: { vs: "vendor/monaco/vs" } }); require(["vs/editor/editor.main"], create); }
  $("#editor-save").onclick = async () => {
    if (!editor || !await allowEdit(`Uložit změny do ${relative}?`)) return;
    await desktop.writeFile({ path: relative, content: editor.getValue(), permissionMode: permissionMode(), confirmed: permissionMode() === "confirm" });
    notify(`Soubor ${relative} byl uložen.`); refreshChangeCard();
  };
}

renderFiles = async function(path = state.currentPath || "::drives", remember = true) {
  desktop?.browser.setVisible(false);
  const c = $("#workspace-content"); c.classList.remove("browser-workspace");
  if (!desktop) { c.innerHTML = '<p class="muted">Soubory jsou dostupné v desktopové aplikaci.</p>'; return; }
  try {
    const data = await desktop.listFiles({ path: path || "::drives" }); state.currentPath = data.path || "::drives";
    if (remember && fileNavigation.history[fileNavigation.index] !== state.currentPath) { fileNavigation.history = fileNavigation.history.slice(0, fileNavigation.index + 1); fileNavigation.history.push(state.currentPath); fileNavigation.index = fileNavigation.history.length - 1; }
    c.innerHTML = `<div class="file-shell"><form id="file-navigation" class="workspace-toolbar file-navigation"><button id="file-back" type="button" ${fileNavigation.index <= 0 ? "disabled" : ""}>←</button><button id="file-forward" type="button" ${fileNavigation.index >= fileNavigation.history.length - 1 ? "disabled" : ""}>→</button><button id="file-up" type="button" ${data.parent == null ? "disabled" : ""}>↑</button><button id="file-computer" type="button">PC</button><input id="file-path" value="${escapeHtml(data.displayPath || data.path)}"><button type="submit">Otevřít</button></form><div class="file-list">${data.entries.map(item => `<div class="workspace-entry" data-file-kind="${item.kind}" data-file-path="${escapeHtml(item.path)}">${item.drive ? "▣" : item.kind === "directory" ? "▣" : "·"} ${escapeHtml(item.name)}<small>${item.kind === "file" ? `${item.size} B` : item.drive ? "Místní disk" : "Složka"}</small></div>`).join("")}</div></div>`;
    $("#file-back").onclick = () => { if (fileNavigation.index > 0) renderFiles(fileNavigation.history[--fileNavigation.index], false); };
    $("#file-forward").onclick = () => { if (fileNavigation.index < fileNavigation.history.length - 1) renderFiles(fileNavigation.history[++fileNavigation.index], false); };
    $("#file-up").onclick = () => renderFiles(data.parent || "::drives");
    $("#file-computer").onclick = () => renderFiles("::drives");
    $("#file-navigation").onsubmit = event => { event.preventDefault(); const value = $("#file-path").value.trim(); renderFiles(value === "Tento počítač" ? "::drives" : value); };
    $$('[data-file-path]').forEach(item => item.onclick = () => item.dataset.fileKind === "directory" ? renderFiles(item.dataset.filePath) : openEditor(item.dataset.filePath));
  } catch (error) { c.innerHTML = `<p class="muted">${escapeHtml(error.message)}</p>`; }
};

const oldRenderWorkspace = renderWorkspace;
renderWorkspace = async function() {
  const c = $("#workspace-content");
  c.classList.remove("browser-workspace");
  $("#workspace-title").textContent = ({ browser: "Prohlížeč", files: "Soubory", output: "Výstup", git: "Git změny", logs: "Logy", memory: "Paměť", artifacts: "Artefakty" })[state.workspace] || "Pracovna";
  $$('[data-workspace]').forEach(button => button.classList.toggle("active", button.dataset.workspace === state.workspace));
  desktop?.browser.setVisible(false);
  if (state.workspace === "browser") { if (desktop) renderBrowser(c); else oldRenderWorkspace(); return; }
  if (state.workspace === "files") { await renderFiles(); return; }
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
  new ResizeObserver(syncBrowserBounds).observe($("#app-shell"));
}
setupWorkspaceResize();
$("#detach-workspace").onclick = () => desktop?.openWorkspaceWindow(state.workspace);
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
  if (action === "terminal") return desktop?.openTerminal();
  if (action === "browser") { state.workspace="browser"; $("#app-shell").classList.remove("workspace-hidden"); return renderWorkspace(); }
  if (action === "find") { const query=prompt("Najít na aktuální stránce:"); if (query) window.find(query,false,false,true); return; }
  if (action === "previous-chat") return selectAdjacentChat(-1);
  if (action === "next-chat") return selectAdjacentChat(1);
  if (action === "back") return state.workspace === "files" && fileNavigation.index > 0 ? renderFiles(fileNavigation.history[--fileNavigation.index], false) : desktop?.browser.action("back");
  if (action === "forward") return state.workspace === "files" && fileNavigation.index < fileNavigation.history.length - 1 ? renderFiles(fileNavigation.history[++fileNavigation.index], false) : desktop?.browser.action("forward");
  if (action === "zoom-in") return desktop ? desktop.zoom("in") : document.body.style.zoom = String((Number(document.body.style.zoom)||1)+.1);
  if (action === "zoom-out") return desktop ? desktop.zoom("out") : document.body.style.zoom = String(Math.max(.6,(Number(document.body.style.zoom)||1)-.1));
  if (action === "zoom-reset") return desktop ? desktop.zoom("reset") : document.body.style.zoom = "1";
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

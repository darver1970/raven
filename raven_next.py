"""Bezplatné lokální centrum schopností pro Raven 1.2.

Modul soustřeďuje nastavení a bezpečné registry nových funkcí. Záměrně
neprovádí nákup, nespouští neověřený pluginový kód a neposílá data na internet.
Experimentální funkce jsou ve výchozím stavu vypnuté.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from raven_network import is_loopback_url, offline_enabled, require_network_url


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
SETTINGS_PATH = RUNTIME / "raven-1.2-settings.json"
MCP_PATH = RUNTIME / "mcp-servers.json"
WORKFLOWS_PATH = RUNTIME / "workflows.json"
PROMPTS_PATH = RUNTIME / "prompt-library.json"
MEMORY_PATH = RUNTIME / "memory-v2.json"
PRIVACY_LOG_PATH = RUNTIME / "privacy-audit.json"
SUPPORT_DIR = RUNTIME / "support"
SKILLS_DIR = RUNTIME / "skills"
EXPORT_DIR = RUNTIME / "exports"
SANDBOX_DIR = RUNTIME / "sandboxes"
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
SECRET = re.compile(
    r"(?i)(api[_ -]?key|authorization|bearer|token|secret|password)(\s*[:=]\s*|\s+)([^\s,;]+)"
)
PROMPT_INJECTION = re.compile(
    r"(?i)(ignore|forget|override|disregard).{0,40}(instructions|rules|system|developer)|"
    r"(system prompt|developer message|reveal.{0,30}(token|secret|password|key))"
)


FEATURES: tuple[dict[str, Any], ...] = (
    {"id": "raven_cortex", "name": "Raven Cortex · důkazní mozek", "group": "Kvalita", "stable": True},
    {"id": "model_manager", "name": "Správce modelů", "group": "Modely", "stable": True},
    {"id": "performance_profiles", "name": "Výkonnostní profily", "group": "Modely", "stable": True},
    {"id": "knowledge_hybrid", "name": "Znalostní knihovna a citace", "group": "Znalosti", "stable": True},
    {"id": "memory_v2", "name": "Typovaná dlouhodobá paměť", "group": "Znalosti", "stable": True},
    {"id": "mcp_manager", "name": "Bezpečný správce MCP", "group": "Rozšíření", "stable": True},
    {"id": "safe_mode", "name": "Bezpečný režim", "group": "Bezpečnost", "stable": True},
    {"id": "task_isolation", "name": "Izolované pracovní kopie", "group": "Bezpečnost", "stable": False},
    {"id": "browser_observer", "name": "Pozorovatelný browser agent", "group": "Web", "stable": False},
    {"id": "visual_workflows", "name": "Vizuální workflow", "group": "Automatizace", "stable": True},
    {"id": "diagnostic_center", "name": "Diagnostické centrum", "group": "Systém", "stable": True},
    {"id": "installer_repair", "name": "Oprava instalace", "group": "Systém", "stable": True},
    {"id": "update_rollback", "name": "Aktualizace s návratem", "group": "Systém", "stable": True},
    {"id": "free_api_budget", "name": "Hlídání bezplatných kvót", "group": "Modely", "stable": True},
    {"id": "durable_tasks", "name": "Obnovitelné agentní úlohy", "group": "Automatizace", "stable": True},
    {"id": "backup_transfer", "name": "Bezpečný přenos nastavení", "group": "Systém", "stable": True},
    {"id": "storage_cleanup", "name": "Správa místa", "group": "Systém", "stable": True},
    {"id": "accessible_ui", "name": "Přístupné rozhraní", "group": "Rozhraní", "stable": True},
    {"id": "multimodal", "name": "Obrázky a lokální OCR", "group": "Média", "stable": False},
    {"id": "document_generation", "name": "Tvorba dokumentů", "group": "Média", "stable": False},
    {"id": "privacy_center", "name": "Centrum soukromí", "group": "Bezpečnost", "stable": True},
    {"id": "prompt_injection", "name": "Ochrana proti prompt injection", "group": "Bezpečnost", "stable": True},
    {"id": "context_manager", "name": "Správce kontextu", "group": "Znalosti", "stable": True},
    {"id": "prompt_library", "name": "Knihovna promptů", "group": "Znalosti", "stable": True},
    {"id": "model_evaluation", "name": "Lokální hodnocení modelů", "group": "Modely", "stable": False},
    {"id": "fact_verification", "name": "Kontrola tvrzení a citací", "group": "Kvalita", "stable": True},
    {"id": "skills_manager", "name": "Správce dovedností", "group": "Rozšíření", "stable": True},
    {"id": "local_api", "name": "Lokální API s tokenem", "group": "Integrace", "stable": False},
    {"id": "lan_mode", "name": "Režim domácí sítě", "group": "Integrace", "stable": False},
    {"id": "windows_integration", "name": "Integrace s Windows", "group": "Integrace", "stable": True},
    {"id": "accessibility", "name": "Přístupnost", "group": "Rozhraní", "stable": True},
    {"id": "experiment_lab", "name": "Experimentální laboratoř", "group": "Rozšíření", "stable": True},
    {"id": "release_automation", "name": "Automatizované vydávání", "group": "Kvalita", "stable": True},
    {"id": "support_mode", "name": "Režim podpory", "group": "Systém", "stable": True},
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _read(path: Path, key: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {key: []}
    except (OSError, json.JSONDecodeError):
        return {key: []}


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def redact(value: Any) -> str:
    return SECRET.sub(r"\1\2[SKRYTO]", str(value))


def default_settings() -> dict[str, Any]:
    experiments = {item["id"]: False for item in FEATURES if not item["stable"]}
    return {
        "schema": 1,
        "performance_profile": "balanced",
        "safe_mode": False,
        "offline_mode": False,
        "memory_enabled": True,
        "high_contrast": False,
        "reduce_motion": False,
        "beginner_mode": False,
        "privacy_consent_ttl_minutes": 60,
        "context_budget_tokens": 8192,
        "max_task_minutes": 30,
        "max_parallel_agents": 2,
        "keep_local_releases": 1,
        "log_retention_days": 14,
        "experiments": experiments,
        "updated_at": "",
    }


def load_settings() -> dict[str, Any]:
    current = {**default_settings(), **_read(SETTINGS_PATH, "settings")}
    known_experiments = default_settings()["experiments"]
    supplied = current.get("experiments", {})
    current["experiments"] = {
        key: bool(supplied.get(key, value)) for key, value in known_experiments.items()
    }
    current["performance_profile"] = (
        current["performance_profile"]
        if current.get("performance_profile") in {"economy", "balanced", "quality", "coding", "private"}
        else "balanced"
    )
    current["context_budget_tokens"] = max(2048, min(131072, int(current.get("context_budget_tokens", 8192))))
    current["max_task_minutes"] = max(1, min(240, int(current.get("max_task_minutes", 30))))
    current["max_parallel_agents"] = max(1, min(2, int(current.get("max_parallel_agents", 2))))
    return current


def save_settings(data: dict[str, Any]) -> dict[str, Any]:
    current = load_settings()
    for key in ("safe_mode", "offline_mode", "memory_enabled", "high_contrast", "reduce_motion", "beginner_mode"):
        if key in data:
            if not isinstance(data[key], bool):
                raise ValueError(f"{key} musí být ano nebo ne.")
            current[key] = data[key]
    if "performance_profile" in data:
        profile = str(data["performance_profile"])
        if profile not in {"economy", "balanced", "quality", "coding", "private"}:
            raise ValueError("Neplatný výkonnostní profil.")
        current["performance_profile"] = profile
    integer_limits = {
        "privacy_consent_ttl_minutes": (5, 1440),
        "context_budget_tokens": (2048, 131072),
        "max_task_minutes": (1, 240),
        "max_parallel_agents": (1, 2),
        "keep_local_releases": (1, 3),
        "log_retention_days": (1, 90),
    }
    for key, (minimum, maximum) in integer_limits.items():
        if key in data:
            current[key] = max(minimum, min(maximum, int(data[key])))
    if "experiments" in data:
        if not isinstance(data["experiments"], dict):
            raise ValueError("Experimenty musí být objekt.")
        known = current["experiments"]
        for key, value in data["experiments"].items():
            if key not in known or not isinstance(value, bool):
                raise ValueError("Neznámý experiment nebo neplatná hodnota.")
            known[key] = value
    if current["safe_mode"]:
        current["offline_mode"] = True
        current["experiments"] = {key: False for key in current["experiments"]}
    current["updated_at"] = _now()
    _write(SETTINGS_PATH, current)
    return current


def _memory_total_gb() -> float:
    try:
        import psutil

        return round(psutil.virtual_memory().total / 1024**3, 1)
    except (ImportError, OSError):
        return 0.0


def system_profile() -> dict[str, Any]:
    ram = _memory_total_gb()
    available_ram = 0.0
    try:
        import psutil
        available_ram = round(psutil.virtual_memory().available / 1024**3, 1)
    except (ImportError, OSError):
        pass
    cores = os.cpu_count() or 1
    disk = shutil.disk_usage(ROOT)
    if ram and ram < 12:
        recommendation = "economy"
    elif ram >= 24 and cores >= 8:
        recommendation = "quality"
    else:
        recommendation = "balanced"
    return {
        "os": platform.platform(),
        "cpu": platform.processor() or "Windows CPU",
        "logical_cores": cores,
        "ram_gb": ram,
        "available_ram_gb": available_ram,
        "model_budget_gb": round(min(ram, available_ram + 2.0), 1) if ram and available_ram else ram,
        "disk_free_gb": round(disk.free / 1024**3, 1),
        "recommended_profile": recommendation,
        "profile_reason": "Doporučení vychází z RAM, počtu vláken a volného místa.",
    }


def ollama_models() -> dict[str, Any]:
    models: list[dict[str, Any]] = []
    error = ""
    try:
        request = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        for item in payload.get("models", []):
            size = int(item.get("size", 0) or 0)
            models.append({
                "name": str(item.get("name", ""))[:160],
                "size_bytes": size,
                "size_gb": round(size / 1024**3, 2),
                "modified_at": str(item.get("modified_at", ""))[:64],
                "digest": str(item.get("digest", ""))[:24],
            })
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        error = redact(exc)
    profile = system_profile()
    return {
        "running": not bool(error),
        "error": error,
        "models": models,
        "total_size_bytes": sum(item["size_bytes"] for item in models),
        "recommended_profile": profile["recommended_profile"],
        "recommendations": [
            {"name": "qwen3.5:4b", "purpose": "rychlý univerzální model", "minimum_ram_gb": 8},
            {"name": "qwen3.5:9b", "purpose": "kvalitnější analýza", "minimum_ram_gb": 16},
            {"name": "qwen2.5-coder:7b", "purpose": "programování", "minimum_ram_gb": 12},
        ],
    }


def manage_ollama_model(data: dict[str, Any]) -> dict[str, Any]:
    action = str(data.get("action", "")).lower()
    model = str(data.get("model", "")).strip()
    if action not in {"pull", "remove"}:
        raise ValueError("Povolená akce modelu je pull nebo remove.")
    if action == "pull" and offline_enabled(load_settings()):
        raise ValueError("Stažení modelu je v offline nebo bezpečném režimu zablokované.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{1,159}", model):
        raise ValueError("Neplatný název modelu.")
    executable = shutil.which("ollama")
    if not executable:
        bundled = ROOT / "runtime" / "ollama" / "ollama.exe"
        executable = str(bundled) if bundled.is_file() else ""
    if not executable:
        raise ValueError("Spustitelná Ollama nebyla nalezena.")
    command = [executable, "pull" if action == "pull" else "rm", model]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
    if result.returncode != 0:
        raise ValueError(redact(result.stderr or result.stdout or "Správa modelu selhala.")[-1000:])
    return {"action": action, "model": model, "status": "completed", "output": redact(result.stdout)[-2000:], "catalog": ollama_models()}


def benchmark_model(data: dict[str, Any]) -> dict[str, Any]:
    model = str(data.get("model", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{1,159}", model):
        raise ValueError("Neplatný název modelu.")
    payload = json.dumps({"model": model, "prompt": "Odpověz pouze slovem OK.", "stream": False, "options": {"num_predict": 8}}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=payload, headers={"Content-Type": "application/json"}, method="POST")
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise ValueError(f"Test modelu selhal: {redact(exc)}") from exc
    elapsed = max(0.001, time.monotonic() - started)
    eval_count = int(result.get("eval_count", 0) or 0)
    return {"model": model, "status": "ok", "seconds": round(elapsed, 2), "tokens_per_second": round(eval_count / elapsed, 2), "response": str(result.get("response", ""))[:80]}


def inspect_untrusted_content(value: Any) -> dict[str, Any]:
    text = str(value or "")
    matches = [match.group(0)[:160] for match in PROMPT_INJECTION.finditer(text)]
    return {
        "trusted": not bool(matches),
        "matches": matches[:20],
        "sanitized": PROMPT_INJECTION.sub("[BLOKOVANÁ NEDŮVĚRYHODNÁ INSTRUKCE]", text),
    }


def feature_overview() -> dict[str, Any]:
    settings = load_settings()
    features = []
    for item in FEATURES:
        enabled = item["stable"] or bool(settings["experiments"].get(item["id"]))
        if settings["safe_mode"] and item["group"] in {"Rozšíření", "Web", "Integrace", "Média"}:
            enabled = False
        features.append({**item, "enabled": enabled, "status": "stable" if item["stable"] else "experimental"})
    return {"version": "1.2", "features": features, "settings": settings, "system": system_profile()}


def _validate_id(value: Any, label: str = "ID") -> str:
    result = str(value or "").strip().lower()
    if not SAFE_ID.fullmatch(result):
        raise ValueError(f"{label} smí obsahovat jen malá písmena, čísla, tečku, pomlčku a podtržítko.")
    return result


def mcp_servers() -> dict[str, Any]:
    data = _read(MCP_PATH, "servers")
    servers = [item for item in data.get("servers", []) if isinstance(item, dict)]
    for item in servers:
        item.pop("env", None)
    return {"servers": servers, "safe_mode": load_settings()["safe_mode"]}


def save_mcp_server(data: dict[str, Any]) -> dict[str, Any]:
    server_id = _validate_id(data.get("id"), "ID MCP serveru")
    transport = str(data.get("transport", "stdio"))
    if transport not in {"stdio", "http"}:
        raise ValueError("Podporovaný MCP transport je stdio nebo http.")
    command = str(data.get("command", "")).strip()[:500]
    url = str(data.get("url", "")).strip()[:1000]
    if transport == "stdio" and not command:
        raise ValueError("Stdio MCP server potřebuje příkaz.")
    if transport == "http" and not re.match(r"^https?://", url):
        raise ValueError("HTTP MCP server potřebuje platnou adresu.")
    if transport == "http" and not is_loopback_url(url):
        require_network_url(url, load_settings(), "uložení vzdáleného MCP serveru")
    current = _read(MCP_PATH, "servers")
    servers = [item for item in current.get("servers", []) if isinstance(item, dict) and item.get("id") != server_id]
    entry = {
        "id": server_id,
        "name": str(data.get("name", server_id)).strip()[:120],
        "transport": transport,
        "command": command,
        "url": url,
        "enabled": data.get("enabled") is True,
        "permissions": sorted({str(item) for item in data.get("permissions", []) if str(item) in {"files-read", "files-write", "network", "terminal"}}),
        "scope": str(data.get("scope", "chat")) if str(data.get("scope", "chat")) in {"chat", "project", "global"} else "chat",
        "updated_at": _now(),
    }
    if entry["enabled"] and not entry["permissions"]:
        raise ValueError("Povolený MCP server musí mít výslovně vybrané oprávnění.")
    servers.append(entry)
    _write(MCP_PATH, {"servers": servers})
    return mcp_servers()


def test_mcp_server(server_id: str) -> dict[str, Any]:
    server_id = _validate_id(server_id)
    server = next((item for item in _read(MCP_PATH, "servers").get("servers", []) if item.get("id") == server_id), None)
    if not server:
        raise ValueError("MCP server nebyl nalezen.")
    started = time.monotonic()
    if server.get("transport") == "http":
        from urllib.parse import urlparse

        parsed = urlparse(str(server.get("url", "")))
        if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return {"id": server_id, "status": "configured", "message": "Vzdálený server se bez souhlasu nekontaktuje."}
        try:
            with socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or (443 if parsed.scheme == "https" else 80)), timeout=2):
                status = "ok"
        except OSError:
            status = "unavailable"
    else:
        executable = str(server.get("command", "")).split()[0]
        status = "ok" if shutil.which(executable) or Path(executable).is_file() else "unavailable"
    return {"id": server_id, "status": status, "latency_ms": round((time.monotonic() - started) * 1000)}


def delete_mcp_server(server_id: str) -> dict[str, Any]:
    server_id = _validate_id(server_id)
    current = _read(MCP_PATH, "servers")
    current["servers"] = [item for item in current.get("servers", []) if item.get("id") != server_id]
    _write(MCP_PATH, current)
    return mcp_servers()


def workflows() -> dict[str, Any]:
    return _read(WORKFLOWS_PATH, "workflows")


def save_workflow(data: dict[str, Any]) -> dict[str, Any]:
    workflow_id = _validate_id(data.get("id") or uuid4().hex[:12], "ID workflow")
    nodes = data.get("nodes", [])
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 40:
        raise ValueError("Workflow musí obsahovat 1 až 40 kroků.")
    allowed = {"input", "knowledge", "memory", "prompt", "model", "approval", "review", "output"}
    cleaned = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or str(node.get("type")) not in allowed:
            raise ValueError("Workflow obsahuje nepovolený typ kroku.")
        cleaned.append({"id": _validate_id(node.get("id") or f"step-{index + 1}"), "type": str(node["type"]), "label": str(node.get("label", node["type"]))[:120], "config": {key: redact(value)[:1000] for key, value in dict(node.get("config", {})).items() if key not in {"api_key", "token", "secret"}}})
    current = workflows()
    values = [item for item in current.get("workflows", []) if item.get("id") != workflow_id]
    values.append({"id": workflow_id, "name": str(data.get("name", workflow_id))[:120], "nodes": cleaned, "enabled": data.get("enabled") is True, "updated_at": _now()})
    _write(WORKFLOWS_PATH, {"workflows": values})
    return workflows()


def run_workflow(workflow_id: str, simulate: bool = True) -> dict[str, Any]:
    workflow_id = _validate_id(workflow_id)
    workflow = next((item for item in workflows().get("workflows", []) if item.get("id") == workflow_id), None)
    if not workflow:
        raise ValueError("Workflow nebylo nalezeno.")
    steps = [{"id": node["id"], "type": node["type"], "status": "planned" if simulate else "ready"} for node in workflow["nodes"]]
    return {"id": workflow_id, "simulate": bool(simulate), "status": "simulated" if simulate else "waiting_for_agent", "steps": steps, "requires_approval": any(node["type"] == "approval" for node in workflow["nodes"])}


def prompt_library() -> dict[str, Any]:
    return _read(PROMPTS_PATH, "prompts")


def save_prompt(data: dict[str, Any]) -> dict[str, Any]:
    prompt_id = _validate_id(data.get("id") or uuid4().hex[:12], "ID promptu")
    content = str(data.get("content", "")).strip()
    if not content or len(content) > 12000:
        raise ValueError("Prompt musí obsahovat 1 až 12000 znaků.")
    current = prompt_library()
    prompts = [item for item in current.get("prompts", []) if item.get("id") != prompt_id]
    prompts.append({"id": prompt_id, "name": str(data.get("name", prompt_id))[:120], "category": str(data.get("category", "general"))[:48], "content": content, "version": int(data.get("version", 1)), "updated_at": _now()})
    _write(PROMPTS_PATH, {"prompts": prompts})
    return prompt_library()


def memories(query: str = "") -> dict[str, Any]:
    current = _read(MEMORY_PATH, "memories")
    now = datetime.now(timezone.utc).astimezone()
    values = []
    terms = set(re.findall(r"[a-z0-9á-ž_-]{3,}", query.lower()))
    for item in current.get("memories", []):
        expires = str(item.get("expires_at", ""))
        if expires:
            try:
                if datetime.fromisoformat(expires) <= now and not item.get("pinned"):
                    continue
            except ValueError:
                pass
        haystack = f"{item.get('title', '')} {item.get('content', '')}".lower()
        words = set(re.findall(r"[a-z0-9á-ž_-]{3,}", haystack))
        relevance = len(terms & words) / max(1, len(terms)) if terms else 1.0
        if terms and not relevance:
            continue
        values.append({**item, "relevance": round(relevance, 4)})
    values.sort(key=lambda item: (bool(item.get("pinned")), float(item.get("relevance", 0)), str(item.get("updated_at", ""))), reverse=True)
    return {"memories": values[:500]}


def save_memory(data: dict[str, Any]) -> dict[str, Any]:
    memory_id = _validate_id(data.get("id") or uuid4().hex[:12], "ID paměti")
    kind = str(data.get("type", "fact"))
    if kind not in {"fact", "preference", "decision", "plan", "result", "temporary"}:
        raise ValueError("Neplatný typ paměti.")
    scope = str(data.get("scope", "project"))
    if scope not in {"user", "project", "agent", "chat"}:
        raise ValueError("Neplatný rozsah paměti.")
    content = str(data.get("content", "")).strip()
    if not content or len(content) > 8000:
        raise ValueError("Paměť musí obsahovat 1 až 8000 znaků.")
    current = _read(MEMORY_PATH, "memories")
    fingerprint = re.sub(r"\W+", " ", content.lower()).strip()
    values = [item for item in current.get("memories", []) if item.get("id") != memory_id and item.get("fingerprint") != fingerprint]
    values.append({"id": memory_id, "type": kind, "scope": scope, "title": str(data.get("title", kind))[:160], "content": content, "source": str(data.get("source", "user"))[:160], "pinned": data.get("pinned") is True, "expires_at": str(data.get("expires_at", ""))[:64], "fingerprint": fingerprint, "updated_at": _now()})
    _write(MEMORY_PATH, {"memories": values[-500:]})
    return memories()


def delete_memory(memory_id: str) -> dict[str, Any]:
    memory_id = _validate_id(memory_id, "ID paměti")
    current = _read(MEMORY_PATH, "memories")
    values = [item for item in current.get("memories", []) if str(item.get("id", "")) != memory_id]
    if len(values) == len(current.get("memories", [])):
        raise ValueError("Paměť nebyla nalezena.")
    _write(MEMORY_PATH, {"memories": values})
    return memories()


def context_estimate(data: dict[str, Any]) -> dict[str, Any]:
    items = data.get("items", []) if isinstance(data.get("items", []), list) else []
    normalized = []
    total_chars = 0
    for item in items[:100]:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", ""))
        chars = len(content)
        total_chars += chars
        normalized.append({"name": str(item.get("name", "context"))[:120], "type": str(item.get("type", "text"))[:32], "characters": chars, "estimated_tokens": max(1, round(chars / 3.6))})
    tokens = max(0, round(total_chars / 3.6))
    budget = load_settings()["context_budget_tokens"]
    return {"items": normalized, "estimated_tokens": tokens, "budget_tokens": budget, "usage_percent": round(tokens / budget * 100, 1) if budget else 0, "overflow": tokens > budget}


def record_privacy_event(data: dict[str, Any]) -> dict[str, Any]:
    current = _read(PRIVACY_LOG_PATH, "events")
    event = {
        "id": uuid4().hex,
        "created_at": _now(),
        "destination": str(data.get("destination", "local"))[:120],
        "purpose": str(data.get("purpose", "model_request"))[:120],
        "online": data.get("online") is True,
        "fields": sorted({str(item)[:80] for item in data.get("fields", [])}),
        "redacted": data.get("redacted") is not False,
    }
    current["events"] = [*current.get("events", [])[-499:], event]
    _write(PRIVACY_LOG_PATH, current)
    return event


def privacy_events() -> dict[str, Any]:
    current = _read(PRIVACY_LOG_PATH, "events")
    events = current.get("events", [])[-500:]
    return {"events": events, "online_count": sum(1 for item in events if item.get("online")), "redacted_count": sum(1 for item in events if item.get("redacted"))}


def skills_catalog() -> dict[str, Any]:
    skills = []
    if SKILLS_DIR.is_dir():
        for manifest in SKILLS_DIR.glob("*/manifest.json"):
            try:
                value = json.loads(manifest.read_text(encoding="utf-8"))
                skill_id = _validate_id(value.get("id"), "ID dovednosti")
                permissions = sorted({str(item) for item in value.get("permissions", []) if str(item) in {"files-read", "files-write", "network", "terminal"}})
                skills.append({"id": skill_id, "name": str(value.get("name", skill_id))[:120], "version": str(value.get("version", "0"))[:32], "permissions": permissions, "enabled": value.get("enabled") is True, "valid": True})
            except (OSError, ValueError, json.JSONDecodeError):
                skills.append({"id": manifest.parent.name, "name": manifest.parent.name, "valid": False, "enabled": False, "permissions": []})
    return {"skills": skills, "directory": str(SKILLS_DIR)}


def support_report(base_diagnostics: dict[str, Any] | None = None) -> dict[str, Any]:
    SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    profile = system_profile()
    report = {
        "created_at": _now(),
        "version": "1.2",
        "system": profile,
        "features": feature_overview()["features"],
        "models": ollama_models(),
        "mcp": mcp_servers(),
        "skills": skills_catalog(),
        "diagnostics": base_diagnostics or {},
        "privacy": {"events": len(privacy_events()["events"])},
        "secrets_included": False,
    }
    path = SUPPORT_DIR / "raven-support-latest.json"
    _write(path, report)
    return {"path": str(path), "report": report}


def export_portable_settings() -> dict[str, Any]:
    """Exportuje přenositelné JSON nastavení bez DPAPI klíčů a cache."""
    allowed = [
        "raven-settings.json", "raven-project-memory.json", "raven-chats.json",
        "knowledge-library.json", "raven-1.2-settings.json", "mcp-servers.json",
        "workflows.json", "prompt-library.json", "memory-v2.json",
    ]
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPORT_DIR / f"raven-settings-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    manifest = {"created_at": _now(), "version": "1.2", "files": [], "secrets_included": False}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in allowed:
            source = RUNTIME / name
            if not source.is_file():
                continue
            try:
                value = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            raw = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
            archive.writestr(f"runtime/{name}", raw)
            manifest["files"].append({"name": name, "bytes": len(raw)})
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return {"path": str(path), "files": manifest["files"], "secrets_included": False, "bytes": path.stat().st_size}


def inspect_import_bundle(value: Any) -> dict[str, Any]:
    path = Path(str(value or "")).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".zip":
        raise ValueError("Vyberte existující ZIP export Ravenu.")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if "manifest.json" not in names:
            raise ValueError("Balíček nemá manifest Ravenu.")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        unsafe = [name for name in names if name.startswith("/") or ".." in Path(name).parts or not (name == "manifest.json" or name.startswith("runtime/"))]
        if unsafe:
            raise ValueError("Balíček obsahuje nepovolené cesty.")
    return {"path": str(path), "valid": True, "manifest": manifest, "requires_confirmation": True}


def create_isolated_workspace(data: dict[str, Any]) -> dict[str, Any]:
    source = Path(str(data.get("source", ""))).expanduser().resolve()
    if not source.is_dir() or source.parent == source:
        raise ValueError("Izolovaná kopie vyžaduje konkrétní existující projektovou složku.")
    destination = SANDBOX_DIR / f"task-{uuid4().hex[:12]}"
    destination.mkdir(parents=True)
    skipped_names = {".git", ".venv", "node_modules", "runtime", "desktop-dist", "__pycache__", "target"}
    copied = skipped = bytes_copied = 0
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if any(part in skipped_names for part in relative.parts):
            continue
        if path.is_dir():
            continue
        if copied >= 5000:
            skipped += 1
            continue
        try:
            size = path.stat().st_size
            if size > 10 * 1024 * 1024:
                skipped += 1
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied += 1
            bytes_copied += size
        except OSError:
            skipped += 1
    manifest = {"source": str(source), "destination": str(destination), "created_at": _now(), "copied": copied, "skipped": skipped, "bytes": bytes_copied, "limits": {"files": 5000, "single_file_bytes": 10 * 1024 * 1024}}
    _write(destination / ".raven-sandbox.json", manifest)
    return manifest


def storage_summary() -> dict[str, Any]:
    categories = {
        "models": ROOT / "runtime" / "ollama",
        "logs": ROOT / "runtime" / "logs",
        "cache": ROOT / "runtime" / "cache",
        "support": SUPPORT_DIR,
        "builds": ROOT / "desktop-dist",
    }
    values = []
    for name, path in categories.items():
        size = 0
        files = 0
        if path.is_dir():
            for item in path.rglob("*"):
                try:
                    if item.is_file():
                        size += item.stat().st_size
                        files += 1
                except OSError:
                    pass
        values.append({"id": name, "path": str(path), "files": files, "bytes": size})
    disk = shutil.disk_usage(ROOT)
    return {"categories": values, "total_tracked_bytes": sum(item["bytes"] for item in values), "disk_free_bytes": disk.free}


def cleanup_preview() -> dict[str, Any]:
    safe_targets = [
        ROOT / ".pytest_cache",
        ROOT / "desktop-dist" / "win-unpacked",
        RUNTIME / "test-results",
        RUNTIME / "cache",
    ]
    safe_targets.extend(path for path in RUNTIME.glob("electron-smoke-*") if path.is_dir())
    values = []
    for path in safe_targets:
        if not path.exists():
            continue
        size = 0
        files = 0
        iterator = path.rglob("*") if path.is_dir() else [path]
        for item in iterator:
            try:
                if item.is_file():
                    files += 1
                    size += item.stat().st_size
            except OSError:
                pass
        values.append({"path": str(path.resolve()), "files": files, "bytes": size, "safe": True})
    return {"targets": values, "total_bytes": sum(item["bytes"] for item in values), "requires_confirmation": True}

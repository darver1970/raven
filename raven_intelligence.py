"""Prakticke agentni a znalostni jadro Ravenu 1.0.

Modul je zamerne bez placenych zavislosti. Poskytuje lokalni vyhledavani,
bezpecne souborove nastroje, vratne body a provozni diagnostiku. Sitove modely
zustavaji v ``raven_control.py`` a tento modul jim pouze pripravuje overeny
kontext nebo provede uzivatelem vyslovne pozadovanou lokalni akci.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
LIBRARY_SETTINGS_PATH = RUNTIME / "knowledge-library.json"
LIBRARY_DB_PATH = RUNTIME / "knowledge-library.db"
SNAPSHOT_ROOT = RUNTIME / "snapshots"
DIAGNOSTIC_PATH = RUNTIME / "diagnostic-latest.json"

TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".csv", ".json", ".toml", ".ini", ".cfg",
    ".yaml", ".yml", ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx",
    ".jsx", ".html", ".css", ".scss", ".ps1", ".bat", ".cmd", ".xml",
    ".sql", ".log",
}
SAFE_WRITE_EXTENSIONS = TEXT_EXTENSIONS - {".bat", ".cmd", ".ps1"}
SECRET_PATTERN = re.compile(
    r"(?i)(api[_ -]?key|authorization|bearer|token|secret|password)(\s*[:=]\s*|\s+)([^\s,;]+)"
)


def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else dict(fallback)
    except (OSError, json.JSONDecodeError):
        return dict(fallback)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def redact_secrets(value: str) -> str:
    return SECRET_PATTERN.sub(r"\1\2[SKRYTO]", str(value))


def default_library_settings() -> dict[str, Any]:
    return {
        "enabled": False,
        "online_context": True,
        "watch_changes": True,
        "include_subfolders": True,
        "ocr_enabled": False,
        "max_file_mb": 50,
        "locations": [],
        "extensions": sorted(TEXT_EXTENSIONS),
        "excluded_paths": [],
        "last_indexed_at": "",
        "last_error": "",
    }


def load_library_settings() -> dict[str, Any]:
    settings = {**default_library_settings(), **_read_json(LIBRARY_SETTINGS_PATH, {})}
    settings["locations"] = [str(Path(item).expanduser()) for item in settings.get("locations", []) if str(item).strip()]
    settings["max_file_mb"] = max(1, min(500, int(settings.get("max_file_mb", 50))))
    return settings


def save_library_settings(data: dict[str, Any]) -> dict[str, Any]:
    current = load_library_settings()
    for key in ("enabled", "online_context", "watch_changes", "include_subfolders", "ocr_enabled"):
        if key in data:
            current[key] = data[key] is True
    if "max_file_mb" in data:
        current["max_file_mb"] = max(1, min(500, int(data["max_file_mb"])))
    if "locations" in data:
        locations: list[str] = []
        for raw in data.get("locations", []):
            path = Path(str(raw)).expanduser().resolve()
            if path.is_dir() and str(path) not in locations:
                locations.append(str(path))
        current["locations"] = locations
        current["enabled"] = bool(locations) and current.get("enabled", True)
    if "extensions" in data:
        current["extensions"] = sorted({str(item).lower() for item in data.get("extensions", []) if str(item).startswith(".")})
    if "excluded_paths" in data:
        current["excluded_paths"] = [str(Path(item).expanduser().resolve()) for item in data.get("excluded_paths", []) if str(item).strip()]
    _atomic_json(LIBRARY_SETTINGS_PATH, current)
    return current


def rebuild_library_index() -> dict[str, Any]:
    settings = load_library_settings()
    if not settings["locations"]:
        raise ValueError("Nejdrive vyberte alespon jednu slozku znalostni knihovny.")
    allowed = set(settings["extensions"])
    excluded = [Path(item) for item in settings["excluded_paths"]]
    limit = int(settings["max_file_mb"]) * 1024 * 1024
    LIBRARY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(LIBRARY_DB_PATH)
    indexed = skipped = errors = removed = unchanged = 0
    try:
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS documents USING fts5(path UNINDEXED, modified UNINDEXED, content)")
        existing = {str(row[0]): str(row[1]) for row in connection.execute("SELECT path, modified FROM documents").fetchall()}
        seen: set[str] = set()
        for root_text in settings["locations"]:
            root = Path(root_text)
            iterator = root.rglob("*") if settings["include_subfolders"] else root.glob("*")
            for path in iterator:
                try:
                    if not path.is_file() or path.suffix.lower() not in allowed:
                        skipped += 1
                        continue
                    resolved = path.resolve()
                    if any(resolved == item or item in resolved.parents for item in excluded):
                        skipped += 1
                        continue
                    stat = resolved.stat()
                    if stat.st_size > limit:
                        skipped += 1
                        continue
                    resolved_text = str(resolved)
                    seen.add(resolved_text)
                    modified = str(stat.st_mtime_ns)
                    if existing.get(resolved_text) == modified:
                        unchanged += 1
                        continue
                    content = redact_secrets(resolved.read_text(encoding="utf-8", errors="replace"))
                    connection.execute("DELETE FROM documents WHERE path = ?", (resolved_text,))
                    connection.execute(
                        "INSERT INTO documents(path, modified, content) VALUES (?, ?, ?)",
                        (resolved_text, modified, content),
                    )
                    indexed += 1
                except (OSError, UnicodeError):
                    errors += 1
        for missing in set(existing) - seen:
            connection.execute("DELETE FROM documents WHERE path = ?", (missing,))
            removed += 1
        connection.commit()
        settings["last_indexed_at"] = datetime.now().isoformat(timespec="seconds")
        settings["last_error"] = "" if not errors else f"{errors} souboru neslo precist"
        _atomic_json(LIBRARY_SETTINGS_PATH, settings)
    finally:
        connection.close()
    return {"indexed": indexed, "unchanged": unchanged, "removed": removed, "skipped": skipped, "errors": errors, "settings": settings}


def _fts_query(text: str) -> str:
    tokens = re.findall(r"[\wá-žÁ-Ž]{3,}", text, flags=re.UNICODE)
    unique: list[str] = []
    for token in tokens:
        if token.lower() not in {item.lower() for item in unique}:
            unique.append(token)
    return " OR ".join(f'"{item.replace(chr(34), "")}"' for item in unique[:10])


def search_library(query: str, limit: int = 8) -> dict[str, Any]:
    text = str(query).strip()
    expression = _fts_query(text)
    if not expression or not LIBRARY_DB_PATH.exists():
        return {"query": text, "results": []}
    connection = sqlite3.connect(LIBRARY_DB_PATH)
    try:
        rows = connection.execute(
            "SELECT path, snippet(documents, 2, '[', ']', ' … ', 42), bm25(documents), content "
            "FROM documents WHERE documents MATCH ? ORDER BY bm25(documents) LIMIT ?",
            (expression, max(1, min(20, int(limit)))),
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        connection.close()
    results = []
    query_tokens = re.findall(r"[\wá-žÁ-Ž]{3,}", text, flags=re.UNICODE)
    for row in rows:
        content = str(row[3] or "")
        positions = [content.lower().find(token.lower()) for token in query_tokens]
        positions = [position for position in positions if position >= 0]
        position = min(positions) if positions else 0
        line = content.count("\n", 0, position) + 1
        results.append({"path": row[0], "snippet": row[1], "score": row[2], "line": line, "citation": f"{row[0]}:{line}"})
    return {"query": text, "results": results}


def known_desktop() -> Path:
    # Windows stores the effective Desktop known-folder location here, including
    # OneDrive and administrator redirects. USERPROFILE\Desktop is only a
    # fallback and may be a different, invisible directory.
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            configured = str(winreg.QueryValueEx(key, "Desktop")[0]).strip()
        if configured:
            desktop = Path(os.path.expandvars(configured)).expanduser()
            if desktop.is_dir():
                return desktop.resolve()
    except (ImportError, OSError, TypeError, ValueError):
        pass
    candidates = [
        Path(os.environ.get("OneDrive", "")) / "Desktop" if os.environ.get("OneDrive") else Path("__missing__"),
        Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop",
        Path.home() / "Plocha",
    ]
    for path in candidates:
        if path.is_dir():
            return path.resolve()
    desktop = candidates[0]
    desktop.mkdir(parents=True, exist_ok=True)
    return desktop.resolve()


def _mentions_desktop(text: str) -> bool:
    return bool(re.search(r"\b(?:ploše|plose|plochu|plocha|plochy|desktop|desktopu)\b", text, re.IGNORECASE))


def _extract_folder_name(text: str) -> str:
    quoted = re.search(r"[`\"']([^`\"']+)[`\"']", text)
    if quoted:
        value = quoted.group(1)
    else:
        match = re.search(
            r"\b(?:složku|slozku|adresář|adresar|folder|directory)\b\s+"
            r"(?:(?:s\s+názvem|s\s+nazvem|nazvanou|pojmenovanou)\s+)?"
            r"(.+?)(?=\s+(?:na|do|v)\s+(?:ploše|plose|plochu|plochy|desktop|desktopu)\b|$)",
            text,
            re.IGNORECASE,
        )
        value = match.group(1) if match else "Nova slozka"
    value = re.sub(r'[\\/:*?"<>|]', "-", value).strip().strip(".")[:100]
    return value or "Nova slozka"


def _extract_filename(prompt: str) -> str:
    quoted = re.search(r"[`\"']([^`\"']+\.[a-zA-Z0-9]{1,8})[`\"']", prompt)
    plain = re.search(r"(?<![\w.])([^\s\\/:*?\"<>|]+\.(?:txt|md|json|csv|log|html|css|js|py))(?!\w)", prompt, re.IGNORECASE)
    name = (quoted or plain)
    value = name.group(1).strip() if name else "test.txt"
    return Path(value).name


def _extract_content(prompt: str) -> str:
    fenced = re.search(r"```(?:[a-zA-Z0-9_-]+)?\s*\r?\n([\s\S]*?)```", prompt)
    if fenced:
        return fenced.group(1).rstrip()
    patterns = (
        r"(?:s obsahem|obsah(?:em)?|napiš do něj|vloz do nej|vlož do něj)\s*[:=]?\s*[\"']([^\"']*)[\"']",
        r"(?:s obsahem|obsah(?:em)?)\s*[:=]\s*(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, prompt, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    return ""


def detect_local_file_action(prompt: str) -> dict[str, Any] | None:
    text = str(prompt).strip()
    lower = text.lower()
    is_web_page = bool(re.search(r"\b(html|webovou|webová|webove|webové)\s+(stránku|stranku|stránka|stranka|page)\b", lower))
    is_file = bool(re.search(r"\b(soubor|souboru|file)\b", lower)) or is_web_page
    is_folder = bool(re.search(r"\b(složku|slozku|adresář|adresar|folder|directory)\b", lower))
    create = bool(re.search(r"\b(vytvoř|vytvor|udělej|udelej|založ|zaloz|vygeneruj|generuj|create|generate)\b", lower))
    read = bool(re.search(r"\b(přečti|precti|zobraz|otevři|otevri|read)\b", lower))
    edit = bool(re.search(r"\b(uprav|přepiš|prepis|zapiš|zapis|write|edit)\b", lower))
    delete = bool(re.search(r"\b(smaž|smaz|odstraň|odstran|delete|remove)\b", lower))
    if not ((is_file and (create or read or edit or delete)) or (is_folder and create)):
        return None
    absolute = re.search(r"[`\"]([A-Za-z]:\\[^`\"]+\.[A-Za-z0-9]{1,8})[`\"]", text)
    if not absolute:
        absolute = re.search(r"([A-Za-z]:\\[^\s\r\n<>|?*\"]+\.[A-Za-z0-9]{1,8})", text)
    filename = _extract_filename(text)
    if is_folder and create:
        folder_name = _extract_folder_name(text)
        target = (known_desktop() if _mentions_desktop(lower) else ROOT) / folder_name
        return {"action": "create_directory", "path": str(target.resolve())}
    if Path(filename).suffix.lower() not in SAFE_WRITE_EXTENSIONS:
        raise ValueError("Raven muze timto bezpecnym nastrojem pracovat pouze s textovymi soubory.")
    if absolute:
        target = Path(absolute.group(1).strip())
    elif _mentions_desktop(lower):
        target = known_desktop() / filename
    else:
        target = ROOT / filename
    action_name = "create_text_file" if create else "read_text_file" if read else "write_text_file" if edit else "delete_file"
    return {"action": action_name, "path": str(target.resolve()), "content": _extract_content(text)}


def _is_system_or_destructive(target: Path, action: str) -> bool:
    windir = Path(os.environ.get("WINDIR", "C:\\Windows")).resolve()
    program_files = [Path(value).resolve() for value in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")) if value]
    resolved = target.resolve()
    protected = resolved == windir or windir in resolved.parents or any(resolved == item or item in resolved.parents for item in program_files)
    return protected or action in {"delete", "delete_file", "format", "registry", "service"}


def execute_file_action(action: dict[str, Any], permission_mode: str, confirmed: bool = False, simulate: bool = False) -> dict[str, Any]:
    mode = permission_mode if permission_mode in {"full", "confirm", "denied"} else "denied"
    target = Path(str(action.get("path", ""))).expanduser().resolve()
    if mode == "denied":
        raise PermissionError("Souborova akce je v rezimu Zakazano vypnuta.")
    needs_confirmation = mode == "confirm" or _is_system_or_destructive(target, str(action.get("action", "")))
    if needs_confirmation and not confirmed:
        return {"status": "confirmation_required", "action": action["action"], "path": str(target), "message": f"Akce vyzaduje potvrzeni: {target}"}
    if simulate:
        return {"status": "simulated", "action": action["action"], "path": str(target), "message": f"Simulace: akce {action['action']} by pracovala s {target}"}
    if action["action"] in {"create_text_file", "write_text_file"}:
        target.parent.mkdir(parents=True, exist_ok=True)
        content = str(action.get("content", ""))
        if target.exists() and action["action"] == "create_text_file":
            raise FileExistsError(f"Soubor uz existuje a nebyl prepsan: {target}")
        if target.exists():
            backup = SNAPSHOT_ROOT / "file-edits" / datetime.now().strftime("%Y%m%d-%H%M%S-%f") / target.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)
        verified = target.is_file() and target.read_text(encoding="utf-8", errors="replace") == content
        if not verified:
            raise OSError("Soubor se po zapisu nepodarilo overit.")
        verb = "vytvoren" if action["action"] == "create_text_file" else "upraven"
        return {"status": "completed", "action": action["action"], "path": str(target), "bytes": target.stat().st_size, "verified": True, "message": f"Soubor byl {verb} a overen: {target}"}
    if action["action"] == "read_text_file":
        if not target.is_file() or target.suffix.lower() not in TEXT_EXTENSIONS:
            raise FileNotFoundError(f"Textovy soubor nebyl nalezen: {target}")
        if target.stat().st_size > 50 * 1024 * 1024:
            raise ValueError("Soubor je pro prime nacteni prilis velky.")
        content = redact_secrets(target.read_text(encoding="utf-8", errors="replace"))
        return {"status": "completed", "action": action["action"], "path": str(target), "verified": True, "content": content[:24000], "message": f"Obsah souboru {target}:\n\n{content[:24000]}"}
    if action["action"] == "create_directory":
        target.mkdir(parents=True, exist_ok=False)
        return {"status": "completed", "action": action["action"], "path": str(target), "verified": target.is_dir(), "message": f"Slozka byla vytvorena a overena: {target}"}
    if action["action"] == "delete_file":
        if not target.is_file():
            raise FileNotFoundError(f"Soubor nebyl nalezen: {target}")
        trash = RUNTIME / "trash" / datetime.now().strftime("%Y%m%d-%H%M%S-%f") / target.name
        trash.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(trash))
        if target.exists() or not trash.exists():
            raise OSError("Presun do obnovitelneho kose se nepodarilo overit.")
        return {"status": "completed", "action": action["action"], "path": str(target), "recovery_path": str(trash), "verified": True, "message": f"Soubor byl odstranen obnovitelne. Zaloha: {trash}"}
    raise ValueError("Nepodporovana lokalni akce.")


def create_project_snapshot(label: str = "automatic", keep: int = 10) -> dict[str, Any]:
    SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^a-zA-Z0-9_-]", "-", label)[:40] or "snapshot"
    destination = SNAPSHOT_ROOT / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{safe_label}"
    ignored = {".git", "runtime", "desktop-dist", "node_modules", "__pycache__"}
    copied = 0
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in ignored for part in path.relative_to(ROOT).parts):
            continue
        relative = path.relative_to(ROOT)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    snapshots = sorted((item for item in SNAPSHOT_ROOT.iterdir() if item.is_dir()), key=lambda item: item.stat().st_mtime, reverse=True)
    for stale in snapshots[max(1, min(50, keep)):]:
        shutil.rmtree(stale, ignore_errors=True)
    return {"path": str(destination), "files": copied, "kept": min(len(snapshots), keep)}


def run_diagnostics(full: bool = False) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for label, url in (
        ("HUD", "http://127.0.0.1:5174/"),
        ("Raven API", "http://127.0.0.1:8126/settings"),
        ("Ollama/OpenJarvis", "http://127.0.0.1:8000/v1/models"),
        ("Ollama", "http://127.0.0.1:11434/api/tags"),
    ):
        try:
            with urllib.request.urlopen(url, timeout=2 if not full else 5) as response:
                checks.append({"name": label, "status": "ok", "detail": f"HTTP {response.status}"})
        except Exception as error:  # provozni diagnostika musi pokracovat i pri chybe jedne sluzby
            checks.append({"name": label, "status": "error", "detail": str(error)[:180]})
    for label, path in (("Projekt", ROOT), ("Runtime", RUNTIME), ("Znalostni index", LIBRARY_DB_PATH)):
        checks.append({"name": label, "status": "ok" if path.exists() else ("warning" if label == "Znalostni index" else "error"), "detail": str(path)})
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "full": bool(full),
        "status": "error" if any(item["status"] == "error" for item in checks) else "ok",
        "checks": checks,
    }
    _atomic_json(DIAGNOSTIC_PATH, payload)
    return payload

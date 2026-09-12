"""Omezený a ověřitelný builder malých aplikací pro Raven."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from pydantic import BaseModel, Field


ALLOWED_SUFFIXES = {".html", ".css", ".js", ".json", ".md", ".txt", ".py"}
MAX_FILES = 16
MAX_FILE_CHARS = 120_000
MAX_TOTAL_CHARS = 500_000


class GeneratedFile(BaseModel):
    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=MAX_FILE_CHARS)


class ApplicationBlueprint(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str = Field(default="web", pattern=r"^(web|python|mixed)$")
    files: list[GeneratedFile] = Field(min_length=1, max_length=MAX_FILES)
    test_instructions: list[str] = Field(default_factory=list, max_length=12)


def safe_project_name(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9á-žÁ-Ž_-]+", "-", str(value).strip(), flags=re.UNICODE).strip("-_")
    if not normalized:
        normalized = "nova-aplikace"
    return normalized[:80]


def safe_file_path(value: str) -> str:
    supplied = str(value).replace("\\", "/")
    reserved = {"CON", "PRN", "AUX", "NUL", *[f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)]}
    if any(not part or part in {".", ".."} or part.endswith((" ", "."))
           or re.search(r'[<>:"|?*\x00-\x1f]', part)
           or part.split(".")[0].upper() in reserved for part in supplied.split("/")):
        raise ValueError(f"Nebezpečná Windows cesta souboru aplikace: {value}")
    if supplied.startswith("/") or re.match(r"^[A-Za-z]:", supplied):
        raise ValueError(f"Nebezpečná cesta souboru aplikace: {value}")
    raw = supplied.strip("/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Nebezpečná cesta souboru aplikace: {value}")
    if len(path.parts) > 6 or path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ValueError(f"Nepovolený typ nebo hloubka souboru aplikace: {value}")
    return path.as_posix()


def parse_blueprint(value: str | dict[str, Any]) -> ApplicationBlueprint:
    if isinstance(value, str):
        text = value.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1)
        else:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                text = text[start:end + 1]
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError("Builder nevrátil platný JSON projektu.") from error
    blueprint = ApplicationBlueprint.model_validate(value)
    seen: set[str] = set()
    total = 0
    normalized = []
    for item in blueprint.files:
        relative = safe_file_path(item.path)
        key = relative.casefold()
        if key in seen:
            raise ValueError(f"Projekt obsahuje duplicitní soubor: {relative}")
        seen.add(key)
        total += len(item.content)
        normalized.append(item.model_copy(update={"path": relative}))
    if total > MAX_TOTAL_CHARS:
        raise ValueError("Vygenerovaný projekt je příliš velký.")
    if blueprint.kind == "web" and "index.html" not in seen:
        raise ValueError("Webová aplikace musí obsahovat index.html.")
    return blueprint.model_copy(update={"name": safe_project_name(blueprint.name), "files": normalized})


def blueprint_schema() -> dict[str, Any]:
    return ApplicationBlueprint.model_json_schema()


def build_project(
    prompt: str,
    destination: Path,
    generate: Callable[[str, dict[str, Any]], str],
    *,
    overwrite: bool = False,
    node_binary: str = "node",
) -> dict[str, Any]:
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Cílová projektová složka není prázdná.")
    instruction = (
        "Navrhni kompletní malou aplikaci podle zadání. Vrať pouze JSON podle schématu. "
        "Nepřidávej instalační EXE, shellové příkazy, vzdálené trackery, API klíče ani placené služby. "
        "Preferuj samostatnou HTML/CSS/JavaScript aplikaci bez sestavení. Každý soubor musí být úplný.\n\n"
        f"Zadání uživatele:\n{prompt[:8000]}"
    )
    blueprint = parse_blueprint(generate(instruction, blueprint_schema()))
    destination.parent.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, Any]] = []
    # Validate away from the destination. Publishing a directory on the same
    # volume avoids exposing a partially written project on ordinary failures.
    with tempfile.TemporaryDirectory(prefix=".raven-build-", dir=destination.parent) as temporary_root:
        stage = Path(temporary_root)
        for item in blueprint.files:
            target = stage.joinpath(*PurePosixPath(item.path).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item.content, encoding="utf-8", newline="\n")
            written.append({"path": item.path, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "characters": len(item.content)})
        tests = validate_project(stage, blueprint, node_binary=node_binary)
        if not tests["passed"]:
            raise ValueError("Vygenerovaný projekt neprošel kontrolou: " + "; ".join(t["message"] for t in tests["checks"] if not t["passed"]))
        if destination.exists():
            if not destination.is_dir() or any(destination.iterdir()):
                raise ValueError("Cílová projektová složka se během generování změnila; nic nebylo přepsáno.")
            # rmdir refuses a directory that became non-empty concurrently.
            destination.rmdir()
        stage.rename(destination)
    return {"status": "created", "name": blueprint.name, "kind": blueprint.kind, "root": str(destination), "files": written, "validation": tests}


def validate_project(root: Path, blueprint: ApplicationBlueprint, *, node_binary: str = "node") -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    for item in blueprint.files:
        target = root.joinpath(*PurePosixPath(item.path).parts)
        suffix = target.suffix.lower()
        try:
            if suffix == ".json":
                json.loads(target.read_text(encoding="utf-8"))
            elif suffix == ".py":
                ast.parse(target.read_text(encoding="utf-8"), filename=item.path)
            elif suffix == ".js":
                result = subprocess.run([node_binary, "--check", str(target)], capture_output=True, text=True, timeout=20, check=False)
                if result.returncode:
                    raise ValueError((result.stderr or result.stdout).strip())
            elif suffix == ".html":
                content = target.read_text(encoding="utf-8").lower()
                if not all(token in content for token in ("<html", "<body", "</body>", "</html>")):
                    raise ValueError("HTML dokument není úplný.")
            checks.append({"path": item.path, "passed": True, "message": "syntax-ok"})
        except (OSError, ValueError, SyntaxError, subprocess.SubprocessError) as error:
            checks.append({"path": item.path, "passed": False, "message": str(error)[:500]})
    return {"passed": bool(checks) and all(item["passed"] for item in checks), "checks": checks}


def detect_application_request(prompt: str) -> dict[str, str] | None:
    text = str(prompt).strip()
    if re.search(r"(?i)\b(instal(?:ační|acni)?\s+exe|installer)\b", text):
        return None
    if not (re.search(r"(?i)\b(vytvoř|vytvor|udělej|udelej|naprogramuj|postav)\b", text)
            and re.search(r"(?i)\b(aplikaci|aplikace|web|program|nástroj|nastroj)\b", text)):
        return None
    quoted = re.search(r"[\"„“]([^\"„“]{2,80})[\"„“]", text)
    name = safe_project_name(quoted.group(1) if quoted else "raven-aplikace")
    return {"action": "create_application", "name": name}

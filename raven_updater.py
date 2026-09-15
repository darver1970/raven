"""Bezpečné aktualizace portable Raven z veřejných GitHub Releases.

Aktualizátor přijímá jen archiv popsaný manifestem, kontroluje velikost, SHA-256,
cesty i jednotlivé soubory a nikdy nepřepisuje uživatelskou část runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlsplit, unquote
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from contextlib import contextmanager
from functools import wraps



def _load_network_settings(root: Path) -> dict[str, Any]:
    path = Path(root).resolve() / "runtime" / "raven-1.2-settings.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {"offline_mode": True, "safe_mode": True}
    return value if isinstance(value, dict) else {"offline_mode": True, "safe_mode": True}


def _require_network(value: str, root: Path, purpose: str) -> None:
    settings = _load_network_settings(root)
    if settings.get("offline_mode") is True or settings.get("safe_mode") is True:
        raise ValueError(f"{purpose.capitalize()} je v offline nebo bezpečném režimu zablokovaná.")


REPOSITORY = "darver1970/raven"
RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MANIFEST_ASSET = "raven-portable-update.json"
MAX_ARCHIVE_BYTES = 1_500_000_000
MAX_EXPANDED_BYTES = 3_000_000_000
MAX_FILES = 10_000
PRIVATE_ROOT_NAMES = {
    "config.toml", "anon_id", "first_chat_sent", "agents.db", "approvals.db",
    "audit.db", "digest.db", "knowledge.db", "memory.db", "traces.db",
}
PROTECTED_TOP_LEVEL = {"runtime", ".git"}


@contextmanager
def update_lock(root: Path):
    directory = contained_path(root, "runtime/updates")
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = contained_path(root, "runtime/updates/update.lock")
    with lock_path.open("a+b") as stream:
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("Jiná portable aktualizace právě pracuje; nic nebylo změněno.") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def locked_update(function):
    @wraps(function)
    def wrapped(root, *args, **kwargs):
        with update_lock(root):
            return function(root, *args, **kwargs)
    return wrapped


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    fd, name = tempfile.mkstemp(prefix="state-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?", str(value).strip())
    if not match:
        raise ValueError(f"Neplatná verze: {value}")
    return tuple(int(part or 0) for part in match.groups())


def _json_url(url: str, *, timeout: int = 30) -> dict[str, Any]:
    validate_origin_url(url)
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "Raven-Portable-Updater/1.0"})
    try:
        with _open_update_url(request, timeout) as response:
            body = response.read(8_000_001)
            if len(body) > 8_000_000:
                raise ValueError("Aktualizační metadata překročila bezpečný limit.")
            value = json.loads(body.decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise ValueError(f"Stažení aktualizačních dat selhalo: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("Aktualizační odpověď nemá očekávaný formát.")
    return value


def validate_origin_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("Aktualizace vyžaduje důvěryhodnou HTTPS adresu.")
    if url == RELEASE_API:
        return
    prefix = f"/{REPOSITORY}/releases/download/"
    suffix = parsed.path[len(prefix):] if parsed.path.startswith(prefix) else ""
    if (parsed.hostname != "github.com" or not suffix or len(suffix.split("/")) != 2
            or any(part in {"", ".", ".."} or "/" in unquote(part) or "\\" in unquote(part)
                   for part in suffix.split("/"))):
        raise ValueError("Aktualizace nepochází z vydání určeného Raven repozitáře.")


class _UpdateRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlsplit(newurl)
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port not in {None, 443}
                or parsed.hostname not in {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}):
            raise ValueError("Aktualizace byla přesměrována mimo povolené úložiště.")
        if parsed.hostname == "github.com":
            validate_origin_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_update_url(request, timeout):
    return urllib.request.build_opener(_UpdateRedirectHandler()).open(request, timeout=timeout)


def _release_assets(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    assets = release.get("assets", [])
    if not isinstance(assets, list):
        return {}
    return {str(item.get("name", "")): item for item in assets if isinstance(item, dict) and item.get("name")}


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    if manifest.get("schema") != 1:
        raise ValueError("Nepodporovaná verze aktualizačního manifestu.")
    version = str(manifest.get("version", ""))
    _version(version)
    archive = str(manifest.get("archive", ""))
    if not re.fullmatch(r"Raven-Portable-Update-v\d+\.\d+(?:\.\d+)?\.zip", archive):
        raise ValueError("Manifest obsahuje neplatný název archivu.")
    sha256 = str(manifest.get("sha256", "")).lower()
    if not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise ValueError("Manifest neobsahuje platný SHA-256 archivu.")
    size = int(manifest.get("size", 0) or 0)
    if size <= 0 or size > MAX_ARCHIVE_BYTES:
        raise ValueError("Velikost aktualizace je neplatná nebo překračuje bezpečný limit.")
    files = manifest.get("files", {})
    if not isinstance(files, dict) or not files or len(files) > MAX_FILES:
        raise ValueError("Manifest neobsahuje platný seznam souborů.")
    normalized: dict[str, str] = {}
    seen: set[str] = set()
    for relative, digest in files.items():
        safe = safe_relative_path(str(relative))
        if safe.casefold() in seen:
            raise ValueError("Manifest obsahuje kolidující Windows cesty.")
        seen.add(safe.casefold())
        digest = str(digest).lower()
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError(f"Neplatný hash souboru {safe}.")
        normalized[safe] = digest
    return {**manifest, "version": version, "archive": archive, "sha256": sha256, "size": size, "files": normalized}


def safe_relative_path(value: str) -> str:
    supplied = value.replace("\\", "/")
    parts = supplied.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *[f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)]}
    if any(not part or part in {".", ".."} or part.endswith((" ", "."))
           or re.search(r'[<>:"|?*\x00-\x1f]', part)
           or part.split(".")[0].upper() in reserved for part in parts):
        raise ValueError(f"Nebezpečná Windows cesta v aktualizaci: {value}")
    if supplied.startswith("/") or re.match(r"^[A-Za-z]:", supplied):
        raise ValueError(f"Nebezpečná cesta v aktualizaci: {value}")
    raw = supplied.strip("/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Nebezpečná cesta v aktualizaci: {value}")
    if path.parts[0].lower() in PROTECTED_TOP_LEVEL:
        raise ValueError(f"Aktualizace nesmí měnit chráněnou cestu: {value}")
    if len(path.parts) == 1 and path.name.lower() in PRIVATE_ROOT_NAMES:
        raise ValueError(f"Aktualizace nesmí změnit uživatelský soubor: {value}")
    return path.as_posix()


def contained_path(root: Path, relative: str) -> Path:
    """Reject symlinks and junctions before accessing update paths."""
    root = root.resolve()
    target = root
    for part in PurePosixPath(relative).parts:
        target = target / part
        if target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction()):
            raise ValueError(f"Aktualizace nesmí procházet odkazem: {relative}")
    if not target.resolve().is_relative_to(root):
        raise ValueError(f"Cesta opouští kořen aktualizace: {relative}")
    return target


def check(current_version: str, *, release_api: str = RELEASE_API, root: Path | None = None) -> dict[str, Any]:
    if root is not None:
        _require_network(release_api, root, "kontrola aktualizace")
    release = _json_url(release_api)
    assets = _release_assets(release)
    descriptor = assets.get(MANIFEST_ASSET)
    if not descriptor or not descriptor.get("browser_download_url"):
        return {"supported": True, "status": "current", "version": current_version, "message": "Veřejné vydání neobsahuje portable aktualizaci."}
    manifest = validate_manifest(_json_url(str(descriptor["browser_download_url"])))
    available = _version(manifest["version"]) > _version(current_version)
    archive_asset = assets.get(manifest["archive"])
    if available and (not archive_asset or not archive_asset.get("browser_download_url")):
        raise ValueError("Archiv uvedený v manifestu ve vydání chybí.")
    return {
        "supported": True, "status": "available" if available else "current",
        "version": current_version, "availableVersion": manifest["version"],
        "message": f"Raven {manifest['version']} je dostupný." if available else "Používáte nejnovější portable Raven.",
        "manifest": manifest,
        "archive_url": str(archive_asset.get("browser_download_url", "")) if archive_asset else "",
    }


def download_and_stage(update: dict[str, Any], root: Path) -> dict[str, Any]:
    _require_network(str(update.get("archive_url", "")), root, "stažení aktualizace")
    with update_lock(root):
        if contained_path(root, "runtime/updates/transaction.json").exists():
            raise ValueError("Před stažením další aktualizace je nutná recovery přerušené transakce.")
        return _download_and_stage(update, root)


def _download_and_stage(update: dict[str, Any], root: Path) -> dict[str, Any]:
    manifest = validate_manifest(dict(update.get("manifest", {})))
    url = str(update.get("archive_url", ""))
    validate_origin_url(url)
    if unquote(urlsplit(url).path.rsplit("/", 1)[-1]) != manifest["archive"]:
        raise ValueError("Adresa archivu neodpovídá manifestu.")
    updates = contained_path(root, "runtime/updates")
    updates.mkdir(parents=True, exist_ok=True)
    archive = contained_path(root, "runtime/updates/" + manifest["archive"])
    temporary = contained_path(root, "runtime/updates/" + archive.with_suffix(".download").name)
    request = urllib.request.Request(url, headers={"User-Agent": "Raven-Portable-Updater/1.0"})
    total = 0
    try:
        with _open_update_url(request, 120) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise ValueError("Stažený archiv překročil bezpečný limit.")
                if total > manifest["size"]:
                    raise ValueError("Stažený archiv překročil velikost uvedenou v manifestu.")
                output.write(chunk)
        if total != manifest["size"] or _sha256(temporary) != manifest["sha256"]:
            raise ValueError("Velikost nebo SHA-256 stažené aktualizace nesouhlasí.")
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)
    # Never delete or reuse another prepared update's staging directory.
    stage = Path(tempfile.mkdtemp(prefix=f"stage-v{manifest['version']}-", dir=updates))
    expanded = 0
    archived_paths: set[str] = set()
    with zipfile.ZipFile(archive) as bundle:
        infos = bundle.infolist()
        if len(infos) > MAX_FILES:
            raise ValueError("Aktualizace obsahuje příliš mnoho souborů.")
        for info in infos:
            if info.is_dir():
                continue
            relative = safe_relative_path(info.filename)
            if relative.casefold() in archived_paths:
                raise ValueError("Archiv obsahuje duplicitní nebo kolidující cestu.")
            archived_paths.add(relative.casefold())
            if relative not in manifest["files"]:
                raise ValueError(f"Archiv obsahuje soubor mimo manifest: {relative}")
            expanded += info.file_size
            if expanded > MAX_EXPANDED_BYTES or info.external_attr >> 16 & 0o170000 == 0o120000:
                raise ValueError("Aktualizační archiv překročil limit nebo obsahuje odkaz.")
            destination = stage.joinpath(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
    actual = {path.relative_to(stage).as_posix(): _sha256(path) for path in stage.rglob("*") if path.is_file()}
    if actual != manifest["files"]:
        raise ValueError("Obsah rozbalené aktualizace neodpovídá manifestu.")
    state = {"status": "ready", "version": manifest["version"], "stage": str(stage), "manifest": manifest, "prepared_at": _now()}
    atomic_json(contained_path(root, "runtime/updates/pending.json"), state)
    return state


@locked_update
def apply_stage(root: Path, pending_path: Path | None = None) -> dict[str, Any]:
    journal_path = contained_path(root, "runtime/updates/transaction.json")
    if journal_path.exists():
        raise ValueError("Nedokončená aktualizace vyžaduje nejdřív recovery; nic nebude přepsáno.")
    pending_path = pending_path or root / "runtime" / "updates" / "pending.json"
    pending = json.loads(pending_path.read_text(encoding="utf-8-sig"))
    manifest = validate_manifest(dict(pending.get("manifest", {})))
    stage = Path(str(pending.get("stage", ""))).resolve()
    updates_root = contained_path(root, "runtime/updates").resolve()
    if not stage.is_relative_to(updates_root) or not stage.is_dir():
        raise ValueError("Připravená aktualizace není v bezpečné pracovní složce.")
    for relative, digest in manifest["files"].items():
        source = contained_path(stage, relative)
        destination = contained_path(root, relative)
        if not source.is_file() or _sha256(source) != digest:
            raise ValueError(f"Připravený soubor neprošel kontrolou: {relative}")
        if destination.exists() and not destination.is_file():
            raise ValueError(f"Cíl aktualizace není soubor: {relative}")

    changed_files = [
        relative for relative, digest in manifest["files"].items()
        if not contained_path(root, relative).is_file() or _sha256(contained_path(root, relative)) != digest
    ]
    expected_version = f"v{manifest['version']}\n"
    version_path = contained_path(root, "VERSION")
    try:
        version_changed = version_path.read_text(encoding="utf-8-sig") != expected_version
    except OSError:
        version_changed = True

    backup = contained_path(root, "runtime/update-backups") / f"before-v{manifest['version']}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
    backup.mkdir(parents=True, exist_ok=False)
    created: list[str] = []
    replaced: list[str] = []
    # Prepare every backup before the first program-file mutation.
    backup_candidates = [*changed_files, *(["VERSION"] if version_changed else [])]
    for relative in dict.fromkeys(backup_candidates):
        destination = contained_path(root, relative)
        if destination.exists():
            saved = contained_path(backup, relative)
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, saved)
            replaced.append(relative)
        else:
            created.append(relative)
    journal = {"backup": str(backup), "created": created, "replaced": replaced,
               "version": manifest["version"], "status": "applying",
               "backup_hashes": {relative: _sha256(backup / relative) for relative in replaced}}
    atomic_json(journal_path, journal)
    try:
        for relative in changed_files:
            source = stage.joinpath(*PurePosixPath(relative).parts)
            destination = root.joinpath(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".raven-new")
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
        if version_changed:
            version_path.write_text(expected_version, encoding="utf-8")
    except Exception:
        for relative in created:
            root.joinpath(*PurePosixPath(relative).parts).unlink(missing_ok=True)
        for relative in replaced:
            saved = backup.joinpath(*PurePosixPath(relative).parts)
            destination = root.joinpath(*PurePosixPath(relative).parts)
            if saved.exists():
                shutil.copy2(saved, destination)
        journal_path.unlink()
        raise
    result = {
        "status": "applied", "version": manifest["version"], "backup": str(backup),
        "files": len(changed_files), "unchanged": len(manifest["files"]) - len(changed_files),
        "created": created, "replaced": replaced, "completed_at": _now(),
    }
    result["backup_hashes"] = journal["backup_hashes"]
    atomic_json(root / "runtime" / "updates" / "last-result.json", result)
    journal_path.unlink()
    pending_path.unlink(missing_ok=True)
    return result


@locked_update
def rollback_last(root: Path) -> dict[str, Any]:
    if contained_path(root, "runtime/updates/transaction.json").exists():
        raise ValueError("Nejdřív obnov nedokončenou aktualizaci příkazem recover.")
    return _rollback_last(root)


def _rollback_last(root: Path, result_path: Path | None = None) -> dict[str, Any]:
    result_path = result_path or root / "runtime" / "updates" / "last-result.json"
    if not result_path.is_file():
        raise ValueError("Není k dispozici žádná portable aktualizace k návratu.")
    previous = json.loads(result_path.read_text(encoding="utf-8-sig"))
    backup = Path(str(previous.get("backup", ""))).resolve()
    backup_root = contained_path(root, "runtime/update-backups").resolve()
    if not backup.is_relative_to(backup_root) or not backup.is_dir():
        raise ValueError("Záloha aktualizace není v bezpečné složce.")
    created = [safe_relative_path(value) for value in previous.get("created", [])]
    replaced = [safe_relative_path(value) for value in previous.get("replaced", [])]
    # Validate the complete restore set before modifying any destination.
    for relative in created + replaced:
        contained_path(root, relative)
    for relative in replaced:
        source = contained_path(backup, relative)
        if not source.is_file():
            raise ValueError(f"V záloze chybí soubor {relative}.")
        expected = previous.get("backup_hashes", {}).get(relative)
        if expected and _sha256(source) != expected:
            raise ValueError(f"Záloha souboru byla změněna: {relative}.")
    for relative in created:
        root.joinpath(*PurePosixPath(relative).parts).unlink(missing_ok=True)
    for relative in replaced:
        source = backup.joinpath(*PurePosixPath(relative).parts)
        destination = root.joinpath(*PurePosixPath(relative).parts)
        if not source.is_file():
            raise ValueError(f"V záloze chybí soubor {relative}.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".raven-rollback")
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    rollback = {"status": "rolled_back", "from_version": previous.get("version", ""), "files": len(created) + len(replaced), "completed_at": _now()}
    (root / "runtime" / "updates" / "rollback-result.json").write_text(json.dumps(rollback, ensure_ascii=False, indent=2), encoding="utf-8")
    result_path.unlink(missing_ok=True)
    return rollback


@locked_update
def recover_interrupted(root: Path) -> dict[str, Any]:
    journal = contained_path(root, "runtime/updates/transaction.json")
    if not journal.exists():
        return {"status": "no_recovery_needed"}
    transaction = json.loads(journal.read_text(encoding="utf-8"))
    result = _rollback_last(root, journal)
    last = contained_path(root, "runtime/updates/last-result.json")
    if last.exists():
        previous = json.loads(last.read_text(encoding="utf-8"))
        if previous.get("backup") == transaction.get("backup"):
            last.unlink()
    return {**result, "status": "recovered"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "prepare", "apply", "rollback", "recover"))
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--current", default="1.2.0")
    parser.add_argument("--state", default="")
    values = parser.parse_args(argv)
    root = Path(values.root).resolve()
    if values.command == "check":
        result = check(values.current, root=root)
    elif values.command == "prepare":
        if not values.state:
            raise ValueError("Příkaz prepare vyžaduje --state.")
        result = download_and_stage(json.loads(Path(values.state).read_text(encoding="utf-8-sig")), root)
    elif values.command == "apply":
        result = apply_stage(root, Path(values.state).resolve() if values.state else None)
    elif values.command == "recover":
        result = recover_interrupted(root)
    else:
        result = rollback_last(root)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False))
        raise SystemExit(1)

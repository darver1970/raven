"""Typovaný registr skutečných nástrojů pro Raven Brain a Cortex."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


class ToolExecution(BaseModel):
    id: str
    tool: str
    status: str
    started_at: str
    duration_ms: float
    output: dict[str, Any] = Field(default_factory=dict)
    verified: bool = False
    evidence: str = ""
    error: str = ""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    executor: Callable[[BaseModel], dict[str, Any]]
    permission: str = "read"
    risk: str = "low"
    timeout_seconds: int = 60
    requires_confirmation: bool = False
    verifier: Callable[[dict[str, Any]], tuple[bool, str]] | None = None


class ToolRegistry:
    def __init__(self, audit_path: Path | None = None) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._lock = threading.RLock()
        self.audit_path = Path(audit_path).resolve() if audit_path else None

    def register(self, spec: ToolSpec) -> None:
        if not spec.name or spec.name in self._tools:
            raise ValueError(f"Duplicitní nebo prázdný nástroj: {spec.name}")
        if spec.timeout_seconds < 1 or spec.timeout_seconds > 7200:
            raise ValueError("Timeout nástroje musí být 1 až 7200 sekund.")
        self._tools[spec.name] = spec

    def catalog(self) -> list[dict[str, Any]]:
        return [{
            "name": spec.name, "description": spec.description,
            "permission": spec.permission, "risk": spec.risk,
            "timeout_seconds": spec.timeout_seconds,
            "requires_confirmation": spec.requires_confirmation,
            "input_schema": spec.input_model.model_json_schema(),
        } for spec in self._tools.values()]

    def execute(
        self, name: str, arguments: dict[str, Any], *, permission_mode: str,
        confirmed: bool = False,
    ) -> ToolExecution:
        spec = self._tools.get(str(name))
        if spec is None:
            raise ValueError("Nástroj není v povoleném registru.")
        execution_id = uuid4().hex
        started_at = _now()
        started = time.monotonic()
        try:
            if permission_mode not in {"full", "confirm", "denied"}:
                raise ValueError("Neplatný režim oprávnění nástroje.")
            if permission_mode == "denied":
                raise PermissionError("Nástroje jsou v režimu Zakázáno vypnuté.")
            if spec.requires_confirmation and permission_mode == "confirm" and not confirmed:
                raise PermissionError(f"Nástroj {spec.name} vyžaduje potvrzení uživatele.")
            validated = spec.input_model.model_validate(arguments)
            pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="raven-tool")
            future = pool.submit(spec.executor, validated)
            try:
                output = future.result(timeout=spec.timeout_seconds)
            except FutureTimeoutError as error:
                future.cancel()
                raise TimeoutError(f"Nástroj {spec.name} překročil časový limit.") from error
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
            if not isinstance(output, dict):
                raise TypeError("Nástroj musí vrátit objekt.")
            verified, evidence = spec.verifier(output) if spec.verifier else (False, "Nástroj nemá přímý ověřovač.")
            result = ToolExecution(
                id=execution_id, tool=spec.name, status="completed", started_at=started_at,
                duration_ms=round((time.monotonic() - started) * 1000, 2), output=output,
                verified=bool(verified), evidence=str(evidence)[:1000],
            )
        except Exception as error:
            result = ToolExecution(
                id=execution_id, tool=spec.name, status="failed", started_at=started_at,
                duration_ms=round((time.monotonic() - started) * 1000, 2),
                error=str(error)[:2000],
            )
        self._audit(result, spec)
        return result

    def _audit(self, result: ToolExecution, spec: ToolSpec) -> None:
        if self.audit_path is None:
            return
        record = {
            "id": result.id, "at": _now(), "tool": result.tool, "status": result.status,
            "duration_ms": result.duration_ms, "verified": result.verified,
            "permission": spec.permission, "risk": spec.risk, "error": result.error,
        }
        with self._lock:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")

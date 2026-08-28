"""Agentní jádro Ravenu s omezením dvou těžkých úloh a zákazem placených API."""

from __future__ import annotations

import asyncio
import importlib.util
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field, field_validator


FORBIDDEN = re.compile(r"(?:^|[/_.:-])(grok|xai)(?:$|[/_.:-])", re.IGNORECASE)


class AgentTask(BaseModel):
    prompt: str = Field(min_length=1, max_length=12_000)
    agent_id: str = Field(pattern=r"^[a-z0-9-]{2,48}$")
    permission_mode: str = "confirm"
    model: str = "automatic"

    @field_validator("permission_mode")
    @classmethod
    def valid_permission(cls, value: str) -> str:
        if value not in {"full", "confirm", "denied"}:
            raise ValueError("Neplatná úroveň přístupu.")
        return value

    @field_validator("model")
    @classmethod
    def free_model_only(cls, value: str) -> str:
        if FORBIDDEN.search(value) or "paid" in value.lower():
            raise ValueError("Grok, xAI a placené modely jsou zakázané.")
        return value


@dataclass
class RuntimeState:
    active: int = 0
    queued: int = 0
    completed: int = 0
    failed: int = 0
    details: dict[str, Any] = field(default_factory=dict)


class AgentRuntime:
    """Jedna fronta a nejvýše dva současně běžící těžcí agenti."""

    def __init__(self, limit: int = 2) -> None:
        self.limit = max(1, min(2, limit))
        self._semaphore = threading.BoundedSemaphore(self.limit)
        self._state_lock = threading.Lock()
        self.state = RuntimeState()

    async def run(self, task: AgentTask, operation: Callable[[AgentTask], Awaitable[Any]]) -> Any:
        if task.permission_mode == "denied":
            raise PermissionError("Agentní provedení je v režimu Zakázáno vypnuté.")
        with self._state_lock:
            self.state.queued += 1
        await asyncio.to_thread(self._semaphore.acquire)
        try:
            with self._state_lock:
                self.state.queued -= 1
                self.state.active += 1
            try:
                result = await asyncio.wait_for(operation(task), timeout=300)
                with self._state_lock:
                    self.state.completed += 1
                return result
            except Exception:
                with self._state_lock:
                    self.state.failed += 1
                raise
            finally:
                with self._state_lock:
                    self.state.active -= 1
        finally:
            self._semaphore.release()

    def status(self) -> dict[str, Any]:
        packages = {
            "pydantic_ai": "Pydantic AI core",
            "browser_use": "Browser Use",
            "crawl4ai": "Crawl4AI",
            "mcp": "Model Context Protocol",
            "playwright": "Playwright",
        }
        components = [{"id": module, "name": name, "installed": importlib.util.find_spec(module) is not None} for module, name in packages.items()]
        with self._state_lock:
            state = RuntimeState(
                active=self.state.active,
                queued=self.state.queued,
                completed=self.state.completed,
                failed=self.state.failed,
                details=dict(self.state.details),
            )
        return {
            "free_only": True,
            "paid_exception": None,
            "max_heavy_agents": self.limit,
            "active": state.active,
            "queued": state.queued,
            "completed": state.completed,
            "failed": state.failed,
            "components": components,
            "coding_agent": {"name": "Raven Coding", "mode": "built-in", "installed": True},
            "search": {"searxng": "external-local-service", "crawl4ai": "installed"},
        }


RUNTIME = AgentRuntime(limit=2)

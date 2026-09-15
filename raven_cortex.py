"""Raven Cortex: trvalý kognitivní stav, plánování, routing a důkazní kontrola.

Vrstva je záměrně modelově nezávislá. Jazykový model navrhuje obsah, ale
stav, oprávnění, kontrolní body a podmínky dokončení zůstávají deterministické.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


ROOT = Path(__file__).resolve().parent
CORTEX_DB_PATH = ROOT / "runtime" / "raven-cortex.sqlite3"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _words(value: object) -> set[str]:
    return set(re.findall(r"[a-z0-9á-ž_-]{3,}", str(value or "").lower()))


class GoalSpec(BaseModel):
    objective: str = Field(min_length=1, max_length=4000)
    constraints: list[str] = Field(default_factory=list, max_length=40)
    success_criteria: list[str] = Field(default_factory=list, min_length=1, max_length=40)
    unknowns: list[str] = Field(default_factory=list, max_length=30)
    assumptions: list[str] = Field(default_factory=list, max_length=30)
    risk: str = "low"

    @field_validator("risk")
    @classmethod
    def valid_risk(cls, value: str) -> str:
        if value not in {"low", "medium", "high", "critical"}:
            raise ValueError("Neplatná úroveň rizika.")
        return value


class CortexStep(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]{2,64}$")
    title: str = Field(min_length=1, max_length=240)
    agent: str = Field(pattern=r"^[a-z0-9-]{2,64}$")
    capability: str = Field(min_length=2, max_length=80)
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    success_evidence: list[str] = Field(default_factory=list, max_length=20)
    risk: str = "low"
    max_attempts: int = Field(default=2, ge=1, le=4)
    timeout_seconds: int = Field(default=300, ge=1, le=7200)
    status: str = "pending"

    @field_validator("status")
    @classmethod
    def valid_status(cls, value: str) -> str:
        allowed = {"pending", "ready", "running", "waiting_approval", "passed", "failed", "blocked", "skipped"}
        if value not in allowed:
            raise ValueError("Neplatný stav Cortex kroku.")
        return value


class EvidenceRecord(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    task_id: str
    step_id: str = ""
    claim: str = Field(min_length=1, max_length=1200)
    kind: str = Field(pattern=r"^[a-z0-9_-]{2,48}$")
    source: str = Field(min_length=1, max_length=300)
    payload: dict[str, Any] = Field(default_factory=dict)
    verified: bool = False
    strength: float = Field(default=0.5, ge=0.0, le=1.0)
    observed_at: str = Field(default_factory=_now)
    expires_at: str = ""


class StepExecutionResult(BaseModel):
    """Normalizovaný výsledek role; samotné tvrzení modelu není důkaz."""

    ok: bool
    claim: str = Field(min_length=1, max_length=1200)
    kind: str = Field(default="model_response", pattern=r"^[a-z0-9_-]{2,48}$")
    source: str = Field(default="cortex-role", min_length=1, max_length=300)
    verified: bool = False
    strength: float = Field(default=0.5, ge=0.0, le=1.0)
    payload: dict[str, Any] = Field(default_factory=dict)


class ModelProfile(BaseModel):
    id: str
    provider: str
    capabilities: set[str] = Field(default_factory=set)
    min_ram_gb: float = 0.0
    context_tokens: int = 8192
    local: bool = True
    privacy: int = Field(default=5, ge=0, le=5)
    speed: float = Field(default=0.5, ge=0.0, le=1.0)
    quality: float = Field(default=0.5, ge=0.0, le=1.0)
    enabled: bool = True


DEFAULT_MODELS = [
    ModelProfile(id="qwen3.5:0.8b", provider="ollama", capabilities={"chat", "classification", "summarization"}, min_ram_gb=2, context_tokens=32768, speed=0.98, quality=0.38),
    ModelProfile(id="qwen3.5:2b", provider="ollama", capabilities={"chat", "classification", "summarization", "tools"}, min_ram_gb=4, context_tokens=32768, speed=0.92, quality=0.51),
    ModelProfile(id="qwen3.5:4b", provider="ollama", capabilities={"chat", "planning", "reasoning", "tools", "multilingual"}, min_ram_gb=6, context_tokens=32768, speed=0.82, quality=0.64),
    ModelProfile(id="qwen3-vl:4b", provider="ollama", capabilities={"vision", "chat", "tools", "multilingual"}, min_ram_gb=6, context_tokens=32768, speed=0.68, quality=0.67),
    ModelProfile(id="qwen3.5:9b", provider="ollama", capabilities={"chat", "planning", "reasoning", "tools", "multilingual", "vision", "verification"}, min_ram_gb=16, context_tokens=32768, speed=0.45, quality=0.79),
    ModelProfile(id="qwen2.5-coder:7b", provider="ollama", capabilities={"coding", "tools", "debugging", "verification"}, min_ram_gb=10, context_tokens=32768, speed=0.52, quality=0.75),
    ModelProfile(id="deepseek-r1:7b", provider="ollama", capabilities={"reasoning", "math", "planning", "verification"}, min_ram_gb=8, context_tokens=32768, speed=0.35, quality=0.73, enabled=False),
    ModelProfile(id="gemma3:4b", provider="ollama", capabilities={"chat", "vision", "multilingual", "summarization"}, min_ram_gb=6, context_tokens=32768, speed=0.7, quality=0.64, enabled=False),
]


@dataclass(frozen=True)
class ContextItem:
    kind: str
    content: str
    priority: int = 3
    source: str = ""
    created_at: str = ""
    trust: float = 0.5


class CortexStore:
    """Transakční lokální event store s WAL a obnovitelnými kroky."""

    def __init__(self, path: Path = CORTEX_DB_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cortex_tasks (
                    id TEXT PRIMARY KEY, chat_id TEXT NOT NULL DEFAULT '', prompt_hash TEXT NOT NULL,
                    goal_json TEXT NOT NULL, intent TEXT NOT NULL, complexity TEXT NOT NULL,
                    status TEXT NOT NULL, selected_plan TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cortex_steps (
                    task_id TEXT NOT NULL, id TEXT NOT NULL, position INTEGER NOT NULL,
                    data_json TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    idempotency_key TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, id), FOREIGN KEY(task_id) REFERENCES cortex_tasks(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS cortex_evidence (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL, step_id TEXT NOT NULL DEFAULT '',
                    claim TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL,
                    payload_json TEXT NOT NULL, verified INTEGER NOT NULL, strength REAL NOT NULL,
                    observed_at TEXT NOT NULL, expires_at TEXT NOT NULL DEFAULT '', fingerprint TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES cortex_tasks(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_cortex_evidence_task ON cortex_evidence(task_id, step_id);
                CREATE TABLE IF NOT EXISTS cortex_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, event TEXT NOT NULL,
                    data_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cortex_model_scores (
                    model_id TEXT NOT NULL, capability TEXT NOT NULL, successes INTEGER NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0, latency_ms REAL NOT NULL DEFAULT 0,
                    quality REAL NOT NULL DEFAULT 0.5, updated_at TEXT NOT NULL,
                    PRIMARY KEY(model_id, capability)
                );
            """)

    def create_task(self, task_id: str, chat_id: str, prompt: str, goal: GoalSpec, intent: str, complexity: str, plans: dict[str, list[CortexStep]], selected: str) -> None:
        now = _now()
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "INSERT INTO cortex_tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (task_id, chat_id[:128], _hash(prompt), goal.model_dump_json(), intent, complexity, "planned", selected, now, now),
                )
                for position, step in enumerate(plans[selected]):
                    db.execute(
                        "INSERT INTO cortex_steps VALUES (?, ?, ?, ?, 0, ?, ?)",
                        (task_id, step.id, position, step.model_dump_json(), _hash([task_id, step.id, step.model_dump(mode="json")]), now),
                    )
                db.execute("INSERT INTO cortex_events(task_id,event,data_json,created_at) VALUES(?,?,?,?)", (task_id, "task_created", json.dumps({"plans": list(plans), "selected": selected}, ensure_ascii=False), now))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def add_evidence(self, evidence: EvidenceRecord) -> None:
        fingerprint = _hash([evidence.task_id, evidence.step_id, evidence.claim, evidence.kind, evidence.source, evidence.payload])
        with self._lock, self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO cortex_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (evidence.id, evidence.task_id, evidence.step_id, evidence.claim, evidence.kind, evidence.source,
                 json.dumps(evidence.payload, ensure_ascii=False, default=str), int(evidence.verified), evidence.strength,
                 evidence.observed_at, evidence.expires_at, fingerprint),
            )

    def event(self, task_id: str, event: str, data: dict[str, Any] | None = None) -> None:
        with self._lock, self.connect() as db:
            db.execute("INSERT INTO cortex_events(task_id,event,data_json,created_at) VALUES(?,?,?,?)", (task_id, event, json.dumps(data or {}, ensure_ascii=False, default=str), _now()))

    def set_status(self, task_id: str, status: str) -> None:
        with self._lock, self.connect() as db:
            db.execute("UPDATE cortex_tasks SET status=?, updated_at=? WHERE id=?", (status, _now(), task_id))

    def update_step(self, task_id: str, step_id: str, status: str, *, increment_attempt: bool = False) -> dict[str, Any]:
        allowed = {"pending", "ready", "running", "waiting_approval", "passed", "failed", "blocked", "skipped"}
        if status not in allowed:
            raise ValueError("Neplatný stav Cortex kroku.")
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT data_json,attempts FROM cortex_steps WHERE task_id=? AND id=?", (task_id, step_id)).fetchone()
                if not row:
                    raise ValueError("Cortex krok nebyl nalezen.")
                data = json.loads(row["data_json"])
                data["status"] = status
                attempts = int(row["attempts"]) + int(increment_attempt)
                if attempts > int(data.get("max_attempts", 2)) or (status == "failed" and attempts >= int(data.get("max_attempts", 2))):
                    data["status"] = "blocked"
                db.execute("UPDATE cortex_steps SET data_json=?,attempts=?,updated_at=? WHERE task_id=? AND id=?", (json.dumps(data, ensure_ascii=False), attempts, _now(), task_id, step_id))
                db.execute("INSERT INTO cortex_events(task_id,event,data_json,created_at) VALUES(?,?,?,?)", (task_id, "step_updated", json.dumps({"step_id": step_id, "status": data["status"], "attempts": attempts}, ensure_ascii=False), _now()))
                db.execute("COMMIT")
                return {**data, "attempts": attempts}
            except Exception:
                db.execute("ROLLBACK")
                raise

    def recover(self, task_id: str) -> dict[str, Any]:
        task = self.task(task_id)
        if task["status"] not in {"running", "waiting_approval", "interrupted", "needs_verification"}:
            return {"task_id": task_id, "recoverable": False, "status": task["status"]}
        passed = {step["id"] for step in task["steps"] if step["status"] in {"passed", "skipped"}}
        ready = [step for step in task["steps"] if step["status"] not in {"passed", "skipped"} and set(step.get("depends_on", [])) <= passed]
        return {"task_id": task_id, "recoverable": True, "status": task["status"], "next_steps": ready[:3], "completed_steps": sorted(passed)}

    def claim_operation(self, task_id: str, step_id: str) -> None:
        """Atomically claim a production operation before any side effects."""
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                rows = db.execute("SELECT id,data_json,attempts FROM cortex_steps WHERE task_id=?", (task_id,)).fetchall()
                steps = {row["id"]: (json.loads(row["data_json"]), int(row["attempts"])) for row in rows}
                if step_id not in steps:
                    raise ValueError("Cortex krok nebyl nalezen.")
                step, attempts = steps[step_id]
                if step["status"] not in {"pending", "ready", "waiting_approval"}:
                    raise ValueError("Krok již byl spuštěn; před opakováním vyžaduje ověření skutečného stavu.")
                if attempts >= int(step["max_attempts"]):
                    raise ValueError("Cortex krok vyčerpal počet pokusů.")
                if any(dep not in steps or steps[dep][0]["status"] not in {"passed", "skipped"} for dep in step["depends_on"]):
                    raise ValueError("Závislosti Cortex kroku nejsou dokončené.")
                step["status"] = "running"
                db.execute("UPDATE cortex_steps SET data_json=?,attempts=?,updated_at=? WHERE task_id=? AND id=?", (json.dumps(step, ensure_ascii=False), attempts + 1, _now(), task_id, step_id))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def task(self, task_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM cortex_tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise ValueError("Cortex úloha nebyla nalezena.")
            steps = db.execute("SELECT data_json,attempts,idempotency_key FROM cortex_steps WHERE task_id=? ORDER BY position", (task_id,)).fetchall()
            evidence = db.execute("SELECT * FROM cortex_evidence WHERE task_id=? ORDER BY observed_at", (task_id,)).fetchall()
            events = db.execute("SELECT event,data_json,created_at FROM cortex_events WHERE task_id=? ORDER BY seq DESC LIMIT 100", (task_id,)).fetchall()
        return {
            "id": row["id"], "chat_id": row["chat_id"], "goal": json.loads(row["goal_json"]),
            "intent": row["intent"], "complexity": row["complexity"], "status": row["status"],
            "selected_plan": row["selected_plan"], "created_at": row["created_at"], "updated_at": row["updated_at"],
            "steps": [{**json.loads(item["data_json"]), "attempts": item["attempts"], "idempotency_key": item["idempotency_key"]} for item in steps],
            "evidence": [{"id": item["id"], "step_id": item["step_id"], "claim": item["claim"], "kind": item["kind"], "source": item["source"], "payload": json.loads(item["payload_json"]), "verified": bool(item["verified"]), "strength": item["strength"], "observed_at": item["observed_at"]} for item in evidence],
            "events": [{"event": item["event"], "data": json.loads(item["data_json"]), "created_at": item["created_at"]} for item in reversed(events)],
        }

    def status(self) -> dict[str, Any]:
        with self.connect() as db:
            counts = {row["status"]: row["count"] for row in db.execute("SELECT status,COUNT(*) count FROM cortex_tasks GROUP BY status")}
            evidence = db.execute("SELECT COUNT(*) total, SUM(verified) verified FROM cortex_evidence").fetchone()
            recoverable = db.execute("SELECT id,status,updated_at FROM cortex_tasks WHERE status IN ('running','waiting_approval','interrupted') ORDER BY updated_at DESC LIMIT 20").fetchall()
        return {"engine": "Raven Cortex", "schema": 1, "database": str(self.path), "wal": True, "tasks": counts,
                "evidence": {"total": evidence["total"] or 0, "verified": evidence["verified"] or 0},
                "recoverable": [dict(row) for row in recoverable]}

    def model_score(self, model_id: str, capability: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM cortex_model_scores WHERE model_id=? AND capability=?", (model_id, capability)).fetchone()
        return dict(row) if row else {"successes": 0, "failures": 0, "latency_ms": 0.0, "quality": 0.5}

    def record_model_result(self, model_id: str, capability: str, success: bool, latency_ms: float, quality: float) -> None:
        old = self.model_score(model_id, capability)
        total = old["successes"] + old["failures"]
        new_latency = (old["latency_ms"] * total + max(0.0, latency_ms)) / (total + 1)
        new_quality = (old["quality"] * total + max(0.0, min(1.0, quality))) / (total + 1)
        with self._lock, self.connect() as db:
            db.execute("""INSERT INTO cortex_model_scores VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(model_id,capability) DO UPDATE SET successes=excluded.successes,failures=excluded.failures,
                latency_ms=excluded.latency_ms,quality=excluded.quality,updated_at=excluded.updated_at""",
                (model_id, capability, old["successes"] + int(success), old["failures"] + int(not success), new_latency, new_quality, _now()))


def build_goal(prompt: str, intent: str, complexity: str, constraints: Sequence[str] = ()) -> GoalSpec:
    text = str(prompt).strip()
    criteria: dict[str, list[str]] = {
        "file": ["Cílový soubor odpovídá požadavku", "Výsledek byl znovu načten a ověřen"],
        "coding": ["Změna řeší původní příčinu", "Cílené testy prošly", "Regresní testy neodhalily poškození"],
        "research": ["Tvrzení mají dohledatelné zdroje", "Aktuálnost a rozpory byly zkontrolovány"],
        "system": ["Požadovaný provozní stav je přímo změřen", "Nezůstala nedokončená nebo testovací změna"],
        "diagnostic": ["Příčina je oddělena od odhadu", "Diagnózu potvrdil cílený test"],
        "automation": ["Workflow lze bezpečně opakovat", "Chyby a pokračování mají definovaný stav"],
        "chat": ["Odpověď přímo řeší otázku", "Fakta nejsou vydávána za provedené akce"],
    }
    risk = "critical" if re.search(r"(?i)\b(formát|disk|smaž vše|delete all|heslo|platba)\b", text) else "high" if re.search(r"(?i)\b(smaž|instal|publik|github|registry|služb)\b", text) or (complexity == "complex" and intent in {"file", "coding", "system", "automation"}) else "medium" if complexity == "complex" else "low"
    unknowns = []
    if intent in {"file", "system", "coding"} and not re.search(r"[a-zA-Z]:\\|/|\.\w{1,8}\b", text):
        unknowns.append("Přesný cíl musí být zjištěn z bezpečného lokálního kontextu.")
    return GoalSpec(objective=text[:4000], constraints=[str(item)[:500] for item in constraints], success_criteria=criteria.get(intent, criteria["chat"]), unknowns=unknowns, risk=risk)


def _common_final_steps(intent: str) -> list[CortexStep]:
    return [
        CortexStep(id="verify", title="Nezávisle ověřit kritéria úspěchu", agent="verifier", capability="verification", depends_on=["execute"], success_evidence=["verified_postcondition"], risk="low"),
        CortexStep(id="review", title="Zkontrolovat důkazy, rizika a rozsah tvrzení", agent="reviewer", capability="review", depends_on=["verify"], success_evidence=["accepted_review"], risk="low"),
    ]


def build_candidate_plans(goal: GoalSpec, intent: str, complexity: str) -> dict[str, list[CortexStep]]:
    inspect = CortexStep(id="inspect", title="Zjistit skutečný výchozí stav", agent="analyst", capability="diagnostics", success_evidence=["direct_observation"])
    execute = CortexStep(id="execute", title="Provést nejmenší povolenou změnu nebo odpověď", agent="executor", capability="coding" if intent == "coding" else "tools", depends_on=["inspect"], success_evidence=["tool_result"], risk=goal.risk, max_attempts=2)
    if intent == "research":
        execute = execute.model_copy(update={"title": "Získat a porovnat primární zdroje", "agent": "research", "capability": "research", "risk": "low", "success_evidence": ["source_citations"]})
    safe = [inspect, execute, *_common_final_steps(intent)]
    fast = [CortexStep(id="inspect", title="Ověřit klíčový předpoklad", agent="analyst", capability="diagnostics", success_evidence=["direct_observation"]), execute.model_copy(update={"depends_on": ["inspect"]}), *_common_final_steps(intent)]
    if (complexity == "complex" or goal.risk in {"high", "critical"}) and intent != "research":
        safe.insert(1, CortexStep(id="sandbox", title="Připravit vratný nebo izolovaný pracovní prostor", agent="security", capability="sandbox", depends_on=["inspect"], success_evidence=["snapshot_or_isolation"], risk="low"))
        safe[2] = safe[2].model_copy(update={"depends_on": ["sandbox"]})
    return {"safe": safe, "fast": fast}


def score_plan(name: str, steps: Sequence[CortexStep], goal: GoalSpec) -> float:
    score = 1.0 - len(steps) * 0.035
    if any(step.agent == "verifier" for step in steps): score += 0.18
    if goal.risk in {"high", "critical"} and any(step.capability == "sandbox" for step in steps): score += 0.25
    if name == "fast" and goal.risk == "low": score += 0.08
    return round(score, 4)


class ContextCompiler:
    def compile(self, goal: GoalSpec, items: Iterable[ContextItem], max_tokens: int = 8192) -> dict[str, Any]:
        objective_words = _words(goal.objective)
        ranked = []
        seen: set[str] = set()
        for item in items:
            fingerprint = _hash([item.kind, item.content])
            if fingerprint in seen or not item.content.strip():
                continue
            seen.add(fingerprint)
            overlap = len(objective_words & _words(item.content)) / max(1, len(objective_words))
            score = (6 - max(0, min(5, item.priority))) * 2 + overlap * 5 + item.trust * 2
            if item.kind == "rule":
                score += 10
            ranked.append((score, item))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        selected: list[dict[str, Any]] = []
        used = max(1, math.ceil(len(goal.model_dump_json()) / 4))
        for score, item in ranked:
            tokens = max(1, math.ceil(len(item.content) / 4))
            if used + tokens > max_tokens:
                continue
            selected.append({"kind": item.kind, "content": item.content, "source": item.source, "trust": item.trust, "score": round(score, 3)})
            used += tokens
        return {"goal": goal.model_dump(mode="json"), "items": selected, "estimated_tokens": used, "budget": max_tokens, "dropped": len(ranked) - len(selected)}


class CapabilityRouter:
    def __init__(self, store: CortexStore, profiles: Sequence[ModelProfile] = DEFAULT_MODELS) -> None:
        self.store = store
        self.profiles = list(profiles)

    def rank(
        self, capability: str, available: Iterable[str], ram_gb: float, prefer_local: bool = True,
        performance_profile: str = "balanced", complexity: str = "standard",
    ) -> list[dict[str, Any]]:
        available_set = set(available)
        ranked = []
        quality_weight, speed_weight = {
            "economy": (0.12, 0.25), "quality": (0.3, 0.05), "coding": (0.28, 0.06),
            "private": (0.2, 0.1), "balanced": (0.2, 0.1),
        }.get(performance_profile, (0.2, 0.1))
        if complexity == "complex":
            quality_weight += 0.08
            speed_weight = max(0.02, speed_weight - 0.05)
        elif complexity == "simple":
            quality_weight = max(0.08, quality_weight - 0.08)
            speed_weight += 0.15
        for profile in self.profiles:
            if not profile.enabled or profile.id not in available_set or profile.min_ram_gb > ram_gb:
                continue
            history = self.store.model_score(profile.id, capability)
            success_rate = history["successes"] / max(1, history["successes"] + history["failures"])
            capability_fit = 1.0 if capability in profile.capabilities else 0.2
            latency_penalty = min(0.12, float(history["latency_ms"] or 0) / 300_000)
            score = capability_fit * 0.42 + profile.quality * quality_weight + profile.speed * speed_weight + history["quality"] * 0.13 + success_rate * 0.1 + (profile.privacy / 5) * 0.05 - latency_penalty
            if complexity == "simple" and capability in {"chat", "classification", "summarization"}:
                score += 0.05 if profile.id == "qwen3.5:2b" else 0.03 if profile.id == "qwen3.5:0.8b" else 0.0
            if prefer_local and profile.local: score += 0.05
            ranked.append({"model": profile.id, "provider": profile.provider, "score": round(score, 4), "capability_fit": capability_fit, "history": history, "context_tokens": profile.context_tokens, "performance_profile": performance_profile})
        return sorted(ranked, key=lambda item: item["score"], reverse=True)


class EvidenceGraph:
    def evaluate(self, goal: GoalSpec, evidence: Sequence[EvidenceRecord]) -> dict[str, Any]:
        verified = [item for item in evidence if item.verified]
        claims = {}
        for item in evidence:
            claims.setdefault(item.claim, []).append(item)
        claim_scores = {
            claim: round(1 - math.prod(1 - entry.strength for entry in entries if entry.verified), 4)
            for claim, entries in claims.items()
        }
        operational = [item for item in verified if item.kind in {"file", "command", "process", "api", "test", "artifact", "network", "tool"}]
        coverage = min(1.0, len({item.step_id for item in verified if item.step_id}) / max(1, len(goal.success_criteria)))
        confidence = min(1.0, (sum(item.strength for item in verified) / max(1, len(verified))) * 0.65 + coverage * 0.35)
        contradictions = []
        normalized = [(item, _words(item.claim)) for item in evidence]
        for index, (left, left_words) in enumerate(normalized):
            for right, right_words in normalized[index + 1:]:
                overlap = len(left_words & right_words) / max(1, min(len(left_words), len(right_words)))
                negated = bool(re.search(r"(?i)\b(ne|není|nelze|failed|false|not)\b", left.claim)) != bool(re.search(r"(?i)\b(ne|není|nelze|failed|false|not)\b", right.claim))
                if overlap >= 0.6 and negated:
                    contradictions.append({"left": left.id, "right": right.id})
        uncertainty = max(0.0, min(1.0, 1.0 - confidence + min(0.5, len(contradictions) * 0.15)))
        return {"accepted": bool(verified) and not contradictions and (goal.risk == "low" or bool(operational)), "confidence": round(confidence, 4), "uncertainty": round(uncertainty, 4), "verified": len(verified), "operational": len(operational), "contradictions": contradictions, "claim_scores": claim_scores, "coverage": round(coverage, 4)}


class MetacognitiveMonitor:
    def inspect(self, events: Sequence[dict[str, Any]], max_steps: int = 20) -> dict[str, Any]:
        warnings = []
        if len(events) > max_steps * 4:
            warnings.append("Úloha vytváří příliš mnoho událostí.")
        recent_failures = [item for item in events[-12:] if item.get("event") in {"tool_failed", "verification_failed"}]
        fingerprints = [_hash(item.get("data", {})) for item in recent_failures]
        if len(fingerprints) >= 3 and len(set(fingerprints[-3:])) == 1:
            warnings.append("Třikrát se opakuje stejná chyba; úloha má být zablokována místo dalšího pokusu.")
        return {"healthy": not warnings, "warnings": warnings, "event_count": len(events)}


class RavenCortex:
    def __init__(self, store: CortexStore | None = None) -> None:
        self.store = store or CortexStore()
        self.context = ContextCompiler()
        self.router = CapabilityRouter(self.store)
        self.evidence_graph = EvidenceGraph()
        self.metacognition = MetacognitiveMonitor()
        self._execution_lock = threading.Lock()

    def create_task(self, prompt: str, *, chat_id: str = "", intent: str = "chat", complexity: str = "standard", constraints: Sequence[str] = (), task_id: str = "") -> dict[str, Any]:
        goal = build_goal(prompt, intent, complexity, constraints)
        plans = build_candidate_plans(goal, intent, complexity)
        selected = max(plans, key=lambda name: score_plan(name, plans[name], goal))
        task_id = task_id or uuid4().hex
        if not re.fullmatch(r"[a-f0-9]{32}", task_id):
            raise ValueError("Neplatné ID úlohy.")
        self.store.create_task(task_id, chat_id, prompt, goal, intent, complexity, plans, selected)
        result = self.store.task(task_id)
        result["plan_scores"] = {name: score_plan(name, plan, goal) for name, plan in plans.items()}
        return result

    def add_evidence(self, task_id: str, *, claim: str, kind: str, source: str, verified: bool, strength: float = 0.5, step_id: str = "", payload: dict[str, Any] | None = None) -> dict[str, Any]:
        record = EvidenceRecord(task_id=task_id, step_id=step_id, claim=claim[:1200], kind=kind, source=source[:300], verified=verified, strength=strength, payload=payload or {})
        self.store.add_evidence(record)
        self.store.event(task_id, "evidence_added", {"id": record.id, "claim": claim, "verified": verified})
        return record.model_dump(mode="json")

    def finalize(self, task_id: str) -> dict[str, Any]:
        task = self.store.task(task_id)
        goal = GoalSpec.model_validate(task["goal"])
        evidence = [EvidenceRecord(task_id=task_id, step_id=item["step_id"], claim=item["claim"], kind=item["kind"], source=item["source"], payload=item["payload"], verified=item["verified"], strength=item["strength"], observed_at=item["observed_at"]) for item in task["evidence"] if item["kind"] not in {"error", "timeout"}]
        evaluation = self.evidence_graph.evaluate(goal, evidence)
        meta = self.metacognition.inspect(task["events"])
        steps_finished = all(step["status"] in {"passed", "skipped"} for step in task["steps"])
        status = "completed" if steps_finished and evaluation["accepted"] and meta["healthy"] else "needs_verification"
        if any(step["status"] == "blocked" for step in task["steps"]):
            status = "blocked"
        self.store.set_status(task_id, status)
        self.store.event(task_id, "cortex_finalized", {"status": status, "evaluation": evaluation, "metacognition": meta})
        return {"status": status, "evaluation": evaluation, "metacognition": meta}

    def recover(self, task_id: str) -> dict[str, Any]:
        return self.store.recover(task_id)

    def run_operation(self, task_id: str, step_id: str, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """Run a production tool once, respecting persisted DAG dependencies.

        The tool owns permission checks and its execution timeout. An uncertain
        failure is quarantined, never automatically replayed by this wrapper.
        """
        self.store.claim_operation(task_id, step_id)
        self.store.event(task_id, "operation_started", {"step_id": step_id})
        try:
            result = operation()
            if not isinstance(result, dict) or not isinstance(result.get("status"), str):
                raise ValueError("Nástroj nevrátil strukturovaný stav výsledku.")
            status = "waiting_approval" if result["status"] == "confirmation_required" else "skipped" if result["status"] == "simulated" else "passed" if result.get("verified") is True else "blocked"
            self.store.update_step(task_id, step_id, status)
            if status == "blocked":
                self.store.set_status(task_id, "blocked")
            elif status == "waiting_approval":
                self.store.set_status(task_id, "waiting_approval")
            self.store.event(task_id, "operation_finished", {"step_id": step_id, "status": status, "verified": result.get("verified") is True})
            return result
        except BaseException:
            self.store.update_step(task_id, step_id, "blocked")
            self.store.set_status(task_id, "blocked")
            self.store.event(task_id, "operation_uncertain", {"step_id": step_id, "requires_reconciliation": True})
            raise

    def execute(
        self, task_id: str,
        handlers: dict[str, Callable[[dict[str, Any], dict[str, Any]], StepExecutionResult | dict[str, Any]]],
        *, approval: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        # A second caller must not duplicate side effects while the first runs.
        if not self._execution_lock.acquire(blocking=False):
            return {"status": "blocked", "reason": "execution_busy"}
        try:
            return self._execute_locked(task_id, handlers, approval=approval)
        finally:
            self._execution_lock.release()

    def _execute_locked(
        self,
        task_id: str,
        handlers: dict[str, Callable[[dict[str, Any], dict[str, Any]], StepExecutionResult | dict[str, Any]]],
        *,
        approval: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        """Provede plán po závislostech, s limity pokusů, timeouty a důkazy."""
        existing = self.store.task(task_id)
        if any(step["status"] in {"blocked", "running"} for step in existing["steps"]):
            return {"status": "blocked", "reason": "requires_reconciliation", "task": existing}
        self.store.set_status(task_id, "running")
        while True:
            task = self.store.task(task_id)
            completed = {step["id"] for step in task["steps"] if step["status"] in {"passed", "skipped"}}
            unfinished = [step for step in task["steps"] if step["status"] not in {"passed", "skipped", "blocked"}]
            if not unfinished:
                return self.finalize(task_id)
            ready = [step for step in unfinished if set(step.get("depends_on", [])) <= completed]
            if not ready:
                self.store.set_status(task_id, "blocked")
                self.store.event(task_id, "dependency_deadlock", {"unfinished": [step["id"] for step in unfinished]})
                return {"status": "blocked", "reason": "dependency_deadlock", "task": self.store.task(task_id)}

            progressed = False
            for step in ready:
                if int(step["attempts"]) >= int(step["max_attempts"]):
                    updated = self.store.update_step(task_id, step["id"], "blocked")
                    self.store.set_status(task_id, "blocked")
                    return {"status": "blocked", "reason": "attempt_limit", "step": updated}
                if step["status"] == "waiting_approval":
                    if approval is None or not approval(task, step):
                        self.store.set_status(task_id, "waiting_approval")
                        return {"status": "waiting_approval", "step": step, "task": self.store.task(task_id)}
                elif step.get("risk") in {"high", "critical"} and step.get("capability") not in {"sandbox", "verification", "review"}:
                    if approval is None or not approval(task, step):
                        self.store.update_step(task_id, step["id"], "waiting_approval")
                        self.store.set_status(task_id, "waiting_approval")
                        self.store.event(task_id, "approval_required", {"step_id": step["id"], "risk": step.get("risk")})
                        return {"status": "waiting_approval", "step": step, "task": self.store.task(task_id)}

                handler = handlers.get(str(step.get("agent"))) or handlers.get(str(step.get("capability"))) or handlers.get("*")
                if handler is None:
                    self.store.update_step(task_id, step["id"], "blocked")
                    self.store.set_status(task_id, "blocked")
                    self.store.event(task_id, "missing_handler", {"step_id": step["id"], "agent": step.get("agent"), "capability": step.get("capability")})
                    return {"status": "blocked", "reason": "missing_handler", "step": step, "task": self.store.task(task_id)}

                self.store.update_step(task_id, step["id"], "running", increment_attempt=True)
                self.store.event(task_id, "role_started", {"step_id": step["id"], "agent": step.get("agent")})
                pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="raven-cortex-step")
                timed_out = False
                try:
                    future = pool.submit(handler, task, step)
                    raw_result = future.result(timeout=int(step.get("timeout_seconds", 300)))
                    result = raw_result if isinstance(raw_result, StepExecutionResult) else StepExecutionResult.model_validate(raw_result)
                except FutureTimeoutError:
                    future.cancel()
                    timed_out = True
                    result = StepExecutionResult(ok=False, claim="Krok překročil časový limit.", kind="timeout", source=str(step.get("agent", "cortex")), verified=True, strength=1.0)
                except Exception as error:
                    result = StepExecutionResult(ok=False, claim=f"Krok selhal: {error}", kind="error", source=str(step.get("agent", "cortex")), verified=True, strength=0.9)
                finally:
                    pool.shutdown(wait=False, cancel_futures=True)

                # Python cannot forcibly cancel a running callback. Quarantine the
                # task rather than starting another attempt alongside it.
                updated = self.store.update_step(task_id, step["id"], "blocked" if timed_out else ("passed" if result.ok else "failed"))
                self.add_evidence(
                    task_id, step_id=step["id"], claim=result.claim, kind=result.kind,
                    source=result.source, verified=result.verified, strength=result.strength,
                    payload=result.payload,
                )
                self.store.event(task_id, "role_completed" if result.ok else "tool_failed", {
                    "step_id": step["id"], "agent": step.get("agent"), "status": updated["status"],
                    "fingerprint": _hash([step["id"], result.kind, result.claim]),
                })
                if updated["status"] == "blocked":
                    self.store.set_status(task_id, "blocked")
                    return {"status": "blocked", "reason": "timeout_requires_reconciliation" if timed_out else "attempt_limit", "step": updated, "task": self.store.task(task_id)}
                progressed = True
                if not result.ok:
                    break
            if not progressed:
                self.store.set_status(task_id, "blocked")
                return {"status": "blocked", "reason": "no_progress", "task": self.store.task(task_id)}

    def status(self) -> dict[str, Any]:
        return {**self.store.status(), "models": [profile.model_dump(mode="json") for profile in self.router.profiles], "capabilities": ["goal-state", "candidate-plans", "sqlite-wal", "context-compiler", "capability-router", "evidence-graph", "metacognition", "role-execution", "dependency-scheduling", "bounded-retries", "approval-gates"]}


CORTEX = RavenCortex()

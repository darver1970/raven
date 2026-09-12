"""Stavovy orchestrator a dukazova kontrola ukolu pro Raven.

Modul neprovadi nastroje ani sitova volani. Rozhoduje, jaky typ prace je
potreba, pripravi kratky overitelny plan, uchovava kontrolni bod a overuje,
ze vysledna odpoved netvrdi provedeni akce bez skutecneho dukazu.
"""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import re
import secrets
import threading
import unicodedata
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


ROOT = Path(__file__).resolve().parent
BRAIN_TASKS_PATH = ROOT / "runtime" / "raven-brain-tasks.json"
MAX_STORED_TASKS = 200

FORBIDDEN_PROVIDER_PATTERN = re.compile(
    r"(?:^|[/_.:-])(grok|xai|paid)(?:$|[/_.:-])", re.IGNORECASE,
)
SECRET_PATTERN = re.compile(
    r"(?i)(api[_ -]?key|authorization|bearer|token|secret|password)"
    r"(\s*[:=]\s*|\s+)([^\s,;]+)"
)
COMPLETION_CLAIM_PATTERN = re.compile(
    r"(?i)\b("
    r"vytvo(?:r|ř)il(?:a|i|o)?|upravil(?:a|i|o)?|smazal(?:a|i|o)?|"
    r"odstranil(?:a|i|o)?|nainstaloval(?:a|i|o)?|spustil(?:a|i|o)?|"
    r"nahr[aá]l(?:a|i|o)?|odeslal(?:a|i|o)?|přejmenoval(?:a|i|o)?|"
    r"prejmenoval(?:a|i|o)?|provedl(?:a|i|o)?|dokončil(?:a|i|o)?|"
    r"vytvo(?:r|ř)en(?:a|i|o|y)?|upraven(?:a|i|o|y)?|smaz[aá]n(?:a|i|o|y)?|"
    r"odstran(?:ě|e)n(?:a|i|o|y)?|nainstalov[aá]n(?:a|i|o|y)?|"
    r"spu(?:š|s)t(?:ě|e)n(?:a|i|o|y)?|nahr[aá]n(?:a|i|o|y)?|"
    r"odesl[aá]n(?:a|i|o|y)?|p(?:ř|r)ejmenov[aá]n(?:a|i|o|y)?|"
    r"proveden(?:a|i|o|y)?|dokon(?:č|c)en(?:a|i|o|y)?|"
    r"created|updated|deleted|installed|started|uploaded|renamed|completed"
    r")\b"
)


class TaskIntent(str, Enum):
    CHAT = "chat"
    FILE = "file"
    CODING = "coding"
    RESEARCH = "research"
    SYSTEM = "system"
    DIAGNOSTIC = "diagnostic"
    AUTOMATION = "automation"


class TaskComplexity(str, Enum):
    SIMPLE = "simple"
    STANDARD = "standard"
    COMPLEX = "complex"


class TaskStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    WAITING_CONFIRMATION = "waiting_confirmation"
    COMPLETED = "completed"
    NEEDS_VERIFICATION = "needs_verification"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class PlanStep(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]{2,48}$")
    title: str = Field(min_length=1, max_length=160)
    agent_id: str = Field(pattern=r"^[a-z0-9-]{2,48}$")
    requires_tool: bool = False
    status: str = "pending"
    summary: str = ""

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in {"pending", "running", "completed", "failed", "skipped"}:
            raise ValueError("Neplatny stav kroku planu.")
        return value


class ExecutionEvidence(BaseModel):
    kind: str = Field(pattern=r"^[a-z0-9_-]{2,40}$")
    source: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1000)
    verified: bool
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: _now())


class ReviewReport(BaseModel):
    accepted: bool
    confidence: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    verified_evidence_count: int = 0


class BrainTask(BaseModel):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    chat_id: str = Field(default="", max_length=128)
    prompt: str = Field(min_length=1, max_length=12_000)
    request_hash: str = Field(default="", pattern=r"^(?:[a-f0-9]{64})?$")
    confirmation_token_hash: str = Field(default="", pattern=r"^(?:[a-f0-9]{64})?$")
    confirmation_expires_at: str = ""
    confirmation_uses_remaining: int = Field(default=0, ge=0, le=1)
    intent: TaskIntent
    complexity: TaskComplexity
    permission_mode: str
    requested_model: str = "automatic"
    status: TaskStatus = TaskStatus.PLANNED
    plan: list[PlanStep]
    evidence: list[ExecutionEvidence] = Field(default_factory=list)
    result: str = ""
    error: str = ""
    created_at: str = Field(default_factory=lambda: _now())
    updated_at: str = Field(default_factory=lambda: _now())

    @field_validator("permission_mode")
    @classmethod
    def validate_permission(cls, value: str) -> str:
        if value not in {"full", "confirm", "denied"}:
            raise ValueError("Neplatna uroven pristupu.")
        return value


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _plain_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(character for character in text if not unicodedata.combining(character)).lower()


def _redact(value: object) -> str:
    return SECRET_PATTERN.sub(r"\1\2[SKRYTO]", str(value or ""))


def _redact_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_data(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_data(item) for item in value]
    if isinstance(value, str):
        return _redact(value)
    return value


def prompt_fingerprint(prompt: str) -> str:
    """Vrati stabilni otisk puvodniho prikazu bez ukladani jeho tajnych hodnot."""
    normalized = str(prompt or "").strip().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def classify_intent(prompt: str) -> TaskIntent:
    """Klasifikuje ukol deterministicky bez spotreby modelu nebo kreditu."""
    text = _plain_text(prompt)
    file_nouns = r"\b(soubor|slozk|adresar|file|folder|directory|plo[cs]e|desktop)"
    mutation_verbs = r"\b(vytvor|udelej|zaloz|uprav|prepis|zapis|smaz|odstran|presun|prejmenuj|create|edit|delete|move|rename)"
    if re.search(file_nouns, text) and re.search(mutation_verbs, text):
        return TaskIntent.FILE
    if re.search(r"\b(diagnost|troubleshoot|problem|nefunguje|chyba|oprav instal|health check|doctor)\b", text):
        return TaskIntent.DIAGNOSTIC
    if re.search(r"\b(nainstaluj|odinstaluj|proces|sluzb|registry|powershell|terminal|port|restartuj|spust aplikaci|install|process|service)\b", text):
        return TaskIntent.SYSTEM
    if re.search(r"\b(kod|program|skript|python|javascript|typescript|html|css|api|refaktor|debug|testy?|git|commit)\b", text):
        return TaskIntent.CODING
    if re.search(r"\b(projdi internet|vyhledej|hledej na webu|zdroje|citace|aktualni informace|research|search the web|sources)\b", text):
        return TaskIntent.RESEARCH
    if re.search(r"\b(automatiz|naplanuj pravidelne|schedule|kazdy den|po spusteni|workflow)\b", text):
        return TaskIntent.AUTOMATION
    return TaskIntent.CHAT


def classify_complexity(prompt: str, intent: TaskIntent) -> TaskComplexity:
    text = _plain_text(prompt)
    words = re.findall(r"\w+", text)
    complex_markers = (
        "cely projekt", "vsechny soubory", "od zakladu", "kompletni diagnost",
        "architektur", "vice agent", "krok za krokem", "porovnej vice",
    )
    action_count = len(re.findall(
        r"\b(vytvor|uprav|oprav|otestuj|over|spust|nainstaluj|smaz|projdi|porovnej|analyzuj)\b",
        text,
    ))
    if len(words) >= 140 or action_count >= 4 or any(marker in text for marker in complex_markers):
        return TaskComplexity.COMPLEX
    if intent == TaskIntent.CHAT and len(words) <= 24 and action_count == 0:
        return TaskComplexity.SIMPLE
    return TaskComplexity.STANDARD


def _step(step_id: str, title: str, agent_id: str, requires_tool: bool = False) -> PlanStep:
    return PlanStep(id=step_id, title=title, agent_id=agent_id, requires_tool=requires_tool)


def build_plan(intent: TaskIntent, complexity: TaskComplexity) -> list[PlanStep]:
    """Vrati nejmensi plan, ktery stale obsahuje nezavislou kontrolu."""
    plans: dict[TaskIntent, list[PlanStep]] = {
        TaskIntent.CHAT: [
            _step("answer", "Připravit věcnou odpověď", "raven"),
            _step("review", "Zkontrolovat smysl a oporu odpovědi", "reviewer"),
        ],
        TaskIntent.FILE: [
            _step("understand", "Určit přesný soubor a požadovanou změnu", "planner"),
            _step("execute", "Provést povolenou souborovou akci", "files", True),
            _step("verify", "Ověřit výsledek přímo v souborovém systému", "tester", True),
            _step("review", "Porovnat výsledek se zadáním", "reviewer"),
        ],
        TaskIntent.CODING: [
            _step("inspect", "Projít relevantní zdrojový kód a závislosti", "coding", True),
            _step("plan", "Navrhnout nejmenší bezpečnou změnu", "planner"),
            _step("execute", "Implementovat změnu", "coding", True),
            _step("verify", "Spustit cílené a regresní testy", "tester", True),
            _step("review", "Zkontrolovat diff a splnění zadání", "reviewer", True),
        ],
        TaskIntent.RESEARCH: [
            _step("scope", "Vymezit otázku a požadovanou aktuálnost", "planner"),
            _step("research", "Získat informace z vhodných zdrojů", "research", True),
            _step("compare", "Porovnat tvrzení a rozpory", "analyst"),
            _step("review", "Ověřit citace a závěr", "reviewer", True),
        ],
        TaskIntent.SYSTEM: [
            _step("inspect", "Zjistit současný stav systému", "telemetry", True),
            _step("permission", "Ověřit rozsah oprávnění", "security"),
            _step("execute", "Provést schválenou systémovou akci", "automation", True),
            _step("verify", "Ověřit skutečný provozní stav", "tester", True),
        ],
        TaskIntent.DIAGNOSTIC: [
            _step("inspect", "Shromáždit příznaky a provozní důkazy", "telemetry", True),
            _step("analyse", "Určit nejpravděpodobnější příčinu", "analyst"),
            _step("verify", "Ověřit diagnózu cíleným testem", "tester", True),
            _step("review", "Oddělit zjištění od odhadu", "reviewer"),
        ],
        TaskIntent.AUTOMATION: [
            _step("scope", "Vymezit spouštěč, vstupy a omezení", "planner"),
            _step("permission", "Ověřit oprávnění automatizace", "security"),
            _step("execute", "Připravit nebo spustit automatizaci", "automation", True),
            _step("verify", "Ověřit opakovatelnost a chybový stav", "tester", True),
        ],
    }
    selected = [step.model_copy(deep=True) for step in plans[intent]]
    if complexity == TaskComplexity.COMPLEX and intent not in {TaskIntent.CHAT, TaskIntent.RESEARCH}:
        selected.insert(1, _step("context", "Doplnit projektový a provozní kontext", "project-indexer", True))
    return selected


def rank_free_providers(
    available: Iterable[str],
    intent: TaskIntent | str,
    health: dict[str, Any] | None = None,
) -> list[str]:
    """Seradi jen povolene free providery; lokalni zustava posledni."""
    intent_value = TaskIntent(intent) if not isinstance(intent, TaskIntent) else intent
    base_orders: dict[TaskIntent, tuple[str, ...]] = {
        TaskIntent.CODING: ("gemini_free", "openrouter_free", "cerebras_free", "groq_free", "mistral_free", "github_models_free", "cloudflare_free"),
        TaskIntent.RESEARCH: ("gemini_free", "openrouter_free", "mistral_free", "groq_free", "cerebras_free", "github_models_free", "cloudflare_free"),
    }
    default_order = ("gemini_free", "openrouter_free", "groq_free", "cerebras_free", "mistral_free", "github_models_free", "cloudflare_free")
    preferred = base_orders.get(intent_value, default_order)
    allowed = {
        str(provider) for provider in available
        if provider == "local" or not FORBIDDEN_PROVIDER_PATTERN.search(str(provider))
    }
    ordered = [provider for provider in preferred if provider in allowed]
    # Gemini a OpenRouter drzi uzivatelem stanovene prvni poradi. Mene vhodne
    # zalohy se seradi podle zmerene odezvy, pokud maji uspesnou historii.
    pinned = ordered[:2]
    tail = ordered[2:]
    provider_health = health or {}
    tail.sort(key=lambda provider: (
        0 if int(provider_health.get(provider, {}).get("successes", 0)) > 0 else 1,
        float(provider_health.get(provider, {}).get("average_ms", 10**9)),
        preferred.index(provider),
    ))
    result = [*pinned, *tail]
    if "local" in allowed:
        result.append("local")
    return result


class BrainStore:
    """Atomicke uloziste poslednich ukolu, vhodne pro ThreadingHTTPServer."""

    def __init__(self, path: Path = BRAIN_TASKS_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()

    def _load_unlocked(self) -> list[BrainTask]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            values = payload.get("tasks", []) if isinstance(payload, dict) else []
        except (OSError, json.JSONDecodeError):
            return []
        tasks: list[BrainTask] = []
        for item in values if isinstance(values, list) else []:
            if not isinstance(item, dict):
                continue
            try:
                tasks.append(BrainTask.model_validate(item))
            except ValueError:
                continue
        return tasks

    def _save_unlocked(self, tasks: list[BrainTask]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f"{self.path.name}.{uuid4().hex}.tmp")
        payload = {"tasks": [task.model_dump(mode="json") for task in tasks[-MAX_STORED_TASKS:]]}
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def upsert(self, task: BrainTask) -> BrainTask:
        with self._lock:
            tasks = self._load_unlocked()
            tasks = [item for item in tasks if item.id != task.id]
            tasks.append(task)
            self._save_unlocked(tasks)
        return task

    def update(self, task_id: str, operation: Callable[[BrainTask], None]) -> BrainTask:
        """Zmeni jeden ukol pod jedinym zamkem a zabrani ztrate soubezneho stavu."""
        with self._lock:
            tasks = self._load_unlocked()
            position = next((index for index, item in enumerate(tasks) if item.id == task_id), -1)
            if position < 0:
                raise ValueError("Ukol mozku nebyl nalezen.")
            task = tasks[position]
            operation(task)
            tasks[position] = task
            self._save_unlocked(tasks)
        return task

    def get(self, task_id: str) -> BrainTask:
        with self._lock:
            task = next((item for item in self._load_unlocked() if item.id == task_id), None)
        if task is None:
            raise ValueError("Ukol mozku nebyl nalezen.")
        return task

    def list(self, chat_id: str = "", limit: int = 50) -> list[BrainTask]:
        with self._lock:
            tasks = self._load_unlocked()
        if chat_id:
            tasks = [task for task in tasks if task.chat_id == chat_id]
        return tasks[-max(1, min(200, int(limit))):]


class RavenBrain:
    """Koordinator planu, kontrolnich bodu a dukazu."""

    def __init__(self, store: BrainStore | None = None) -> None:
        self.store = store or BrainStore()

    def create_task(
        self,
        prompt: str,
        *,
        chat_id: str = "",
        permission_mode: str = "confirm",
        requested_model: str = "automatic",
    ) -> BrainTask:
        clean_prompt = _redact(prompt).strip()
        if not clean_prompt:
            raise ValueError("Ukol mozku nesmi byt prazdny.")
        intent = classify_intent(clean_prompt)
        complexity = classify_complexity(clean_prompt, intent)
        task = BrainTask(
            id=uuid4().hex,
            chat_id=str(chat_id)[:128],
            prompt=clean_prompt[:12_000],
            request_hash=prompt_fingerprint(prompt),
            intent=intent,
            complexity=complexity,
            permission_mode=permission_mode,
            requested_model=str(requested_model or "automatic")[:120],
            plan=build_plan(intent, complexity),
        )
        return self.store.upsert(task)

    def set_status(self, task_id: str, status: TaskStatus, error: str = "") -> BrainTask:
        task = self.store.get(task_id)
        task.status = status
        task.updated_at = _now()
        task.error = _redact(error)[:2000]
        return self.store.upsert(task)

    def request_confirmation(self, task_id: str, validity_minutes: int = 10) -> tuple[BrainTask, str]:
        """Vytvori jednorazove potvrzeni svazane s ulohou a kratkou platnosti."""
        token = secrets.token_urlsafe(32)
        token_hash = prompt_fingerprint(token)
        minutes = max(1, min(30, validity_minutes))
        expires_at = (datetime.now().astimezone() + timedelta(minutes=minutes)).isoformat(timespec="milliseconds")

        def prepare(task: BrainTask) -> None:
            if task.status != TaskStatus.RUNNING:
                raise ValueError("Potvrzení lze vystavit pouze pro běžící úkol.")
            task.status = TaskStatus.WAITING_CONFIRMATION
            task.confirmation_token_hash = token_hash
            task.confirmation_expires_at = expires_at
            task.confirmation_uses_remaining = 1
            task.updated_at = _now()

        return self.store.update(task_id, prepare), token

    def claim_confirmation(
        self,
        task_id: str,
        *,
        chat_id: str,
        prompt: str,
        confirmation_token: str,
    ) -> BrainTask:
        """Atomicky spotrebuje presne jedno potvrzeni pro presne puvodni zadani."""
        request_hash = prompt_fingerprint(prompt)
        supplied_token = str(confirmation_token or "").strip()

        def claim(task: BrainTask) -> None:
            if task.status != TaskStatus.WAITING_CONFIRMATION:
                raise ValueError("Potvrzovaný úkol už nečeká na potvrzení.")
            if task.chat_id != str(chat_id)[:128]:
                raise ValueError("Potvrzení nepatří k aktuálnímu chatu.")
            if not task.request_hash:
                raise ValueError("Staré potvrzení nelze bezpečně obnovit. Odešlete požadavek znovu.")
            if task.request_hash != request_hash:
                raise ValueError("Potvrzený příkaz se liší od původního požadavku.")
            if not supplied_token or task.confirmation_uses_remaining != 1 or not task.confirmation_token_hash:
                raise ValueError("Potvrzení je neplatné nebo již bylo použito.")
            try:
                expires_at = datetime.fromisoformat(task.confirmation_expires_at)
            except ValueError as error:
                raise ValueError("Potvrzení nemá platnou dobu platnosti.") from error
            if expires_at <= datetime.now().astimezone():
                raise ValueError("Potvrzení vypršelo. Odešlete požadavek znovu.")
            if not hmac.compare_digest(task.confirmation_token_hash, prompt_fingerprint(supplied_token)):
                raise ValueError("Potvrzení neodpovídá tomuto úkolu.")
            task.status = TaskStatus.RUNNING
            task.confirmation_token_hash = ""
            task.confirmation_expires_at = ""
            task.confirmation_uses_remaining = 0
            task.updated_at = _now()

        return self.store.update(task_id, claim)

    @staticmethod
    def public_task(task: BrainTask) -> dict[str, Any]:
        """Vrati stav pro UI bez internich otisku potvrzeni."""
        return task.model_dump(
            mode="json",
            exclude={"request_hash", "confirmation_token_hash", "confirmation_expires_at", "confirmation_uses_remaining"},
        )

    def mark_step(self, task_id: str, step_id: str, status: str, summary: str = "") -> BrainTask:
        task = self.store.get(task_id)
        step = next((item for item in task.plan if item.id == step_id), None)
        if step is None:
            raise ValueError(f"Krok planu nebyl nalezen: {step_id}")
        step.status = status
        step.summary = _redact(summary)[:500]
        task.updated_at = _now()
        return self.store.upsert(task)

    def mark_next_for_agent(self, task_id: str, agent_id: str, status: str, summary: str = "") -> BrainTask:
        task = self.store.get(task_id)
        step = next((item for item in task.plan if item.agent_id == agent_id and item.status in {"pending", "running"}), None)
        if step is None:
            return task
        return self.mark_step(task_id, step.id, status, summary)

    def add_evidence(self, task_id: str, evidence: ExecutionEvidence) -> BrainTask:
        task = self.store.get(task_id)
        safe_evidence = evidence.model_copy(deep=True)
        safe_evidence.source = _redact(safe_evidence.source)[:160]
        safe_evidence.summary = _redact(safe_evidence.summary)[:1000]
        safe_evidence.details = _redact_data(safe_evidence.details)
        task.evidence.append(safe_evidence)
        task.evidence = task.evidence[-40:]
        task.updated_at = _now()
        return self.store.upsert(task)

    def review(self, task_id: str, answer: str) -> ReviewReport:
        task = self.store.get(task_id)
        text = str(answer or "").strip()
        errors: list[str] = []
        warnings: list[str] = []
        if not text:
            errors.append("Vysledna odpoved je prazdna.")
        verified = [item for item in task.evidence if item.verified]
        operational = [
            item for item in verified
            if item.kind in {"file", "tool", "command", "network", "test", "diagnostic", "artifact"}
        ]
        if COMPLETION_CLAIM_PATTERN.search(text) and not operational:
            errors.append("Odpoved tvrdi provedeni akce, ale chybi overeny dukaz nastroje.")
        if task.intent == TaskIntent.RESEARCH and not re.search(r"https?://|\[[^\]]+\]\([^\)]+\)", text):
            errors.append("Vyzkumna odpoved neobsahuje primo dohledatelnou citaci.")
        if re.search(r"(?i)\b(todo|tbd|sem dopln|doplnte zbytek|placeholder)\b", text):
            warnings.append("Odpoved muze obsahovat nedokonceny zastupny text.")
        confidence = "high" if operational and not warnings else "medium" if verified and not errors else "low"
        return ReviewReport(
            accepted=not errors,
            confidence=confidence,
            errors=errors,
            warnings=warnings,
            evidence_count=len(task.evidence),
            verified_evidence_count=len(verified),
        )

    def complete(self, task_id: str, answer: str) -> tuple[BrainTask, ReviewReport]:
        task = self.store.get(task_id)
        report = self.review(task_id, answer)
        task.result = _redact(answer)[:4000]
        task.error = "; ".join(report.errors)[:2000]
        task.status = TaskStatus.COMPLETED if report.accepted else TaskStatus.FAILED
        for step in task.plan:
            if step.agent_id == "reviewer":
                step.status = "completed" if report.accepted else "failed"
                step.summary = f"Důvěra kontroly: {report.confidence}"
            elif step.status == "running":
                step.status = "completed" if report.accepted else "failed"
            elif step.status == "pending":
                step.status = "skipped"
                step.summary = "Krok nebyl pro tuto odpověď potřeba."
        task.updated_at = _now()
        return self.store.upsert(task), report

    def fail(self, task_id: str, error: str) -> BrainTask:
        task = self.store.get(task_id)
        task.status = TaskStatus.FAILED
        task.error = _redact(error)[:2000]
        for step in task.plan:
            if step.status == "running":
                step.status = "failed"
                step.summary = task.error[:500]
                break
        task.updated_at = _now()
        return self.store.upsert(task)

    def recover_interrupted(self) -> int:
        recovered = 0
        for task in self.store.list(limit=MAX_STORED_TASKS):
            if task.status == TaskStatus.RUNNING:
                task.status = TaskStatus.INTERRUPTED
                task.error = "Raven byl ukoncen pred dokoncenim. Ukol lze bezpecne zopakovat."
                task.updated_at = _now()
                self.store.upsert(task)
                recovered += 1
        return recovered

    def status(self, chat_id: str = "") -> dict[str, Any]:
        tasks = self.store.list(chat_id=chat_id, limit=MAX_STORED_TASKS)
        counts = {status.value: 0 for status in TaskStatus}
        for task in tasks:
            counts[task.status.value] += 1
        return {
            "version": "1.0",
            "stateful_orchestrator": True,
            "evidence_required_for_actions": True,
            "free_only_router": True,
            "counts": counts,
            "recoverable": [
                self.public_task(task) for task in tasks
                if task.status in {TaskStatus.INTERRUPTED, TaskStatus.WAITING_CONFIRMATION}
            ][-20:],
        }


BRAIN = RavenBrain()

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest

from raven_brain import (
    BrainStore,
    ExecutionEvidence,
    RavenBrain,
    TaskComplexity,
    TaskIntent,
    TaskStatus,
    build_plan,
    classify_complexity,
    classify_intent,
    prompt_fingerprint,
    rank_free_providers,
)


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Ahoj, jak se máš?", TaskIntent.CHAT),
        ("Vytvoř soubor test.txt na ploše", TaskIntent.FILE),
        ("Refaktoruj tento Python kód a spusť testy", TaskIntent.CODING),
        ("Projdi internet a uveď aktuální zdroje", TaskIntent.RESEARCH),
        ("Proveď diagnostiku, proč aplikace nefunguje", TaskIntent.DIAGNOSTIC),
        ("Nainstaluj program a ověř jeho službu", TaskIntent.SYSTEM),
        ("Naplánuj pravidelně tento workflow každý den", TaskIntent.AUTOMATION),
    ],
)
def test_intent_classification(prompt: str, expected: TaskIntent) -> None:
    assert classify_intent(prompt) == expected


def test_complex_task_detection() -> None:
    prompt = "Projdi celý projekt, oprav všechny soubory, spusť testy a ověř instalaci od základu."
    intent = classify_intent(prompt)
    assert classify_complexity(prompt, intent) == TaskComplexity.COMPLEX
    assert classify_complexity("Kolik je 2 + 2?", TaskIntent.CHAT) == TaskComplexity.SIMPLE


@pytest.mark.parametrize("intent", list(TaskIntent))
def test_every_plan_has_independent_review(intent: TaskIntent) -> None:
    plan = build_plan(intent, TaskComplexity.STANDARD)
    assert plan
    assert plan[-1].agent_id in {"reviewer", "tester"}
    assert len({step.id for step in plan}) == len(plan)


def test_complex_plan_adds_project_context() -> None:
    plan = build_plan(TaskIntent.CODING, TaskComplexity.COMPLEX)
    assert any(step.id == "context" and step.agent_id == "project-indexer" for step in plan)


def test_free_router_keeps_user_priority_and_local_last() -> None:
    available = [
        "local", "openrouter_free", "gemini_free", "groq_free",
        "cerebras_free", "grok", "xai/provider",
    ]
    health = {
        "groq_free": {"successes": 3, "average_ms": 900},
        "cerebras_free": {"successes": 3, "average_ms": 400},
    }
    order = rank_free_providers(available, TaskIntent.CHAT, health)
    assert order[:2] == ["gemini_free", "openrouter_free"]
    assert order.index("cerebras_free") < order.index("groq_free")
    assert order[-1] == "local"
    assert "grok" not in " ".join(order).lower()
    assert "xai" not in " ".join(order).lower()


def make_brain(tmp_path: Path) -> RavenBrain:
    return RavenBrain(BrainStore(tmp_path / "brain-tasks.json"))


def test_task_checkpoint_redacts_secret_and_is_persistent(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    task = brain.create_task(
        "Použij api_key=super-secret-value pro odpověď",
        chat_id="chat-a",
        permission_mode="full",
    )
    loaded = brain.store.get(task.id)
    assert "super-secret-value" not in loaded.prompt
    assert "[SKRYTO]" in loaded.prompt
    assert loaded.request_hash == prompt_fingerprint("Použij api_key=super-secret-value pro odpověď")
    assert loaded.chat_id == "chat-a"
    assert loaded.status == TaskStatus.PLANNED


def test_prompt_fingerprint_detects_changed_confirmation() -> None:
    original = prompt_fingerprint("Vytvoř soubor test.txt")
    assert original == prompt_fingerprint("Vytvoř soubor test.txt")
    assert original != prompt_fingerprint("Smaž soubor test.txt")


def test_confirmation_is_bound_to_chat_prompt_and_single_use(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    prompt = "Vytvoř soubor test.txt"
    task = brain.create_task(prompt, chat_id="chat-a", permission_mode="confirm")
    brain.set_status(task.id, TaskStatus.RUNNING)
    waiting, token = brain.request_confirmation(task.id)
    assert waiting.status == TaskStatus.WAITING_CONFIRMATION
    with pytest.raises(ValueError, match="nepatří"):
        brain.claim_confirmation(task.id, chat_id="chat-b", prompt=prompt, confirmation_token=token)
    with pytest.raises(ValueError, match="liší"):
        brain.claim_confirmation(task.id, chat_id="chat-a", prompt="Smaž soubor test.txt", confirmation_token=token)
    claimed = brain.claim_confirmation(task.id, chat_id="chat-a", prompt=prompt, confirmation_token=token)
    assert claimed.status == TaskStatus.RUNNING
    assert claimed.confirmation_uses_remaining == 0
    with pytest.raises(ValueError, match="nečeká"):
        brain.claim_confirmation(task.id, chat_id="chat-a", prompt=prompt, confirmation_token=token)


def test_concurrent_confirmation_can_be_claimed_once(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    prompt = "Vytvoř soubor test.txt"
    task = brain.create_task(prompt, chat_id="chat-a", permission_mode="confirm")
    brain.set_status(task.id, TaskStatus.RUNNING)
    _, token = brain.request_confirmation(task.id)

    def claim(_: int) -> str:
        try:
            brain.claim_confirmation(task.id, chat_id="chat-a", prompt=prompt, confirmation_token=token)
            return "claimed"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, range(2)))
    assert sorted(results) == ["claimed", "rejected"]


def test_store_skips_only_malformed_task(tmp_path: Path) -> None:
    store = BrainStore(tmp_path / "brain-tasks.json")
    brain = RavenBrain(store)
    valid = brain.create_task("Ahoj", permission_mode="full")
    payload = {
        "tasks": [
            valid.model_dump(mode="json"),
            {"id": "neplatne", "prompt": "Poškozený záznam"},
        ]
    }
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = store.list(limit=10)
    assert [task.id for task in loaded] == [valid.id]


def test_interrupted_task_is_recoverable(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    task = brain.create_task("Oprav kód", permission_mode="full")
    brain.set_status(task.id, TaskStatus.RUNNING)
    assert brain.recover_interrupted() == 1
    recovered = brain.store.get(task.id)
    assert recovered.status == TaskStatus.INTERRUPTED
    assert brain.status()["recoverable"][0]["id"] == task.id


def test_unverified_completion_claim_is_rejected(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    task = brain.create_task("Nainstaluj aplikaci", permission_mode="full")
    brain.add_evidence(task.id, ExecutionEvidence(
        kind="model_response",
        source="gemini_free",
        summary="Model odpověděl.",
        verified=True,
    ))
    report = brain.review(task.id, "Aplikaci jsem nainstaloval a spustil.")
    assert report.accepted is False
    assert report.errors


def test_unverified_passive_completion_claim_is_rejected(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    task = brain.create_task("Vytvoř soubor test.txt", permission_mode="full")
    brain.add_evidence(task.id, ExecutionEvidence(
        kind="model_response",
        source="gemini_free",
        summary="Model odpověděl bez souborového nástroje.",
        verified=True,
    ))
    report = brain.review(task.id, "Soubor byl vytvořen a uložen.")
    assert report.accepted is False
    assert "dukaz nastroje" in report.errors[0]


def test_verified_file_action_is_accepted(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    task = brain.create_task("Vytvoř soubor test.txt", permission_mode="full")
    brain.add_evidence(task.id, ExecutionEvidence(
        kind="file",
        source="create_text_file",
        summary="Soubor existuje a obsah byl přečten zpět.",
        verified=True,
        details={"path": str(tmp_path / "test.txt")},
    ))
    completed, report = brain.complete(task.id, "Soubor byl vytvořen a ověřen.")
    assert report.accepted is True
    assert report.confidence == "high"
    assert completed.status == TaskStatus.COMPLETED
    assert completed.plan[-1].status == "completed"


def test_normal_chat_does_not_require_operational_evidence(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    task = brain.create_task("Kolik je 2 + 2?", permission_mode="denied")
    brain.add_evidence(task.id, ExecutionEvidence(
        kind="model_response",
        source="local",
        summary="Model vrátil neprázdnou odpověď.",
        verified=True,
    ))
    completed, report = brain.complete(task.id, "2 + 2 = 4.")
    assert report.accepted is True
    assert completed.status == TaskStatus.COMPLETED
    assert completed.plan[0].status == "skipped"


def test_research_without_citation_is_rejected(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    task = brain.create_task("Projdi internet a najdi aktuální informace", permission_mode="full")
    brain.add_evidence(task.id, ExecutionEvidence(
        kind="model_response",
        source="gemini_free",
        summary="Model odpověděl.",
        verified=True,
    ))
    report = brain.review(task.id, "Toto je výsledek bez odkazu na zdroj.")
    assert report.accepted is False
    assert report.errors
    assert report.confidence == "low"


def test_evidence_storage_redacts_secrets(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    task = brain.create_task("Běžný dotaz", permission_mode="full")
    brain.add_evidence(task.id, ExecutionEvidence(
        kind="tool",
        source="diagnostic",
        summary="authorization=secret-value",
        verified=True,
        details={"nested": {"api_key": "api_key=another-secret"}},
    ))
    stored = brain.store.get(task.id).evidence[0]
    serialized = stored.model_dump_json()
    assert "secret-value" not in serialized
    assert "another-secret" not in serialized
    assert "[SKRYTO]" in serialized


def test_task_status_can_be_filtered_by_chat(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)
    brain.create_task("Ahoj", chat_id="chat-a", permission_mode="full")
    brain.create_task("Ahoj", chat_id="chat-b", permission_mode="full")
    assert len(brain.store.list(chat_id="chat-a")) == 1
    assert len(brain.store.list(chat_id="chat-b")) == 1


def test_atomic_store_keeps_concurrent_tasks(tmp_path: Path) -> None:
    brain = make_brain(tmp_path)

    def create(index: int) -> str:
        return brain.create_task(f"Běžný dotaz číslo {index}", permission_mode="full").id

    with ThreadPoolExecutor(max_workers=8) as executor:
        task_ids = list(executor.map(create, range(24)))
    stored_ids = {task.id for task in brain.store.list(limit=100)}
    assert len(set(task_ids)) == 24
    assert set(task_ids) <= stored_ids

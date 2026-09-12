from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest

from raven_cortex import (
    CapabilityRouter,
    ContextCompiler,
    ContextItem,
    CortexStore,
    EvidenceGraph,
    EvidenceRecord,
    GoalSpec,
    MetacognitiveMonitor,
    RavenCortex,
    build_candidate_plans,
    build_goal,
    score_plan,
)


def make_cortex(tmp_path: Path) -> RavenCortex:
    return RavenCortex(CortexStore(tmp_path / "cortex.sqlite3"))


def test_complex_risky_goal_prefers_verified_sandbox_plan() -> None:
    goal = build_goal("Nainstaluj aplikaci, publikuj ji na GitHub a vše ověř", "system", "complex")
    plans = build_candidate_plans(goal, "system", "complex")
    assert goal.risk == "high"
    assert any(step.capability == "sandbox" for step in plans["safe"])
    assert score_plan("safe", plans["safe"], goal) > score_plan("fast", plans["fast"], goal)
    assert plans["safe"][-1].agent == "reviewer"


def test_cortex_task_is_transactionally_persistent(tmp_path: Path) -> None:
    cortex = make_cortex(tmp_path)
    task = cortex.create_task("Oprav Python kód a spusť testy", intent="coding", complexity="complex")
    loaded = CortexStore(tmp_path / "cortex.sqlite3").task(task["id"])
    assert loaded["goal"]["success_criteria"]
    assert loaded["selected_plan"] == "safe"
    assert loaded["steps"][-1]["agent"] == "reviewer"
    assert all(step["idempotency_key"] for step in loaded["steps"])


def test_concurrent_cortex_tasks_are_not_lost(tmp_path: Path) -> None:
    cortex = make_cortex(tmp_path)

    def create(index: int) -> str:
        return cortex.create_task(f"Analyzuj úkol {index}", intent="chat", complexity="standard")["id"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        ids = list(executor.map(create, range(20)))
    assert len(set(ids)) == 20
    assert cortex.status()["tasks"]["planned"] == 20


def test_context_compiler_prioritizes_rules_and_relevance() -> None:
    goal = GoalSpec(objective="Opravit instalátor Raven", success_criteria=["Instalace projde"])
    result = ContextCompiler().compile(goal, [
        ContextItem("history", "Nesouvisející dlouhý rozhovor o počasí", priority=5, trust=0.2),
        ContextItem("rule", "Bez slova start nic nespouštěj", priority=0, trust=1.0),
        ContextItem("evidence", "Instalátor Raven hlásí chybějící modul", priority=1, trust=0.9),
    ], max_tokens=200)
    assert result["items"][0]["kind"] == "rule"
    assert any(item["kind"] == "evidence" for item in result["items"])
    assert result["estimated_tokens"] <= result["budget"]


def test_evidence_graph_rejects_high_risk_model_claim_without_tool_evidence() -> None:
    goal = GoalSpec(objective="Nainstalovat Raven", success_criteria=["Aplikace běží"], risk="high")
    model_only = [EvidenceRecord(task_id="a", claim="Instalace je hotová", kind="model_response", source="model", verified=True, strength=0.4)]
    assert EvidenceGraph().evaluate(goal, model_only)["accepted"] is False
    with_api = [*model_only, EvidenceRecord(task_id="a", step_id="verify", claim="API vrací 1.2", kind="api", source="127.0.0.1", verified=True, strength=0.95)]
    result = EvidenceGraph().evaluate(goal, with_api)
    assert result["accepted"] is True
    assert result["operational"] == 1


def test_cortex_finalize_requires_verified_evidence(tmp_path: Path) -> None:
    cortex = make_cortex(tmp_path)
    task = cortex.create_task("Proveď diagnostiku služby", intent="diagnostic", complexity="standard")
    assert cortex.finalize(task["id"])["status"] == "needs_verification"
    cortex.add_evidence(task["id"], step_id="verify", claim="Port a API jsou funkční", kind="api", source="health-check", verified=True, strength=0.95)
    assert cortex.finalize(task["id"])["status"] == "needs_verification"
    for step in task["steps"]:
        cortex.store.update_step(task["id"], step["id"], "passed")
    assert cortex.finalize(task["id"])["status"] == "completed"


def test_capability_router_uses_ram_capability_and_history(tmp_path: Path) -> None:
    store = CortexStore(tmp_path / "cortex.sqlite3")
    router = CapabilityRouter(store)
    available = {"qwen3.5:4b", "qwen3.5:9b", "qwen2.5-coder:7b"}
    assert router.rank("reasoning", available, ram_gb=8)[0]["model"] == "qwen3.5:4b"
    coding = router.rank("coding", available, ram_gb=12)
    assert coding[0]["model"] == "qwen2.5-coder:7b"
    store.record_model_result("qwen2.5-coder:7b", "coding", True, 500, 1.0)
    improved = router.rank("coding", available, ram_gb=12)
    assert improved[0]["history"]["successes"] == 1


def test_metacognition_stops_repeated_identical_failure() -> None:
    events = [{"event": "tool_failed", "data": {"reason": "same"}} for _ in range(3)]
    result = MetacognitiveMonitor().inspect(events)
    assert result["healthy"] is False
    assert "Třikrát" in result["warnings"][0]


def test_recovery_returns_only_dependency_ready_steps(tmp_path: Path) -> None:
    cortex = RavenCortex(CortexStore(tmp_path / "cortex.sqlite3"))
    task = cortex.create_task("Napiš a otestuj kód", intent="coding", complexity="complex")
    cortex.store.set_status(task["id"], "interrupted")
    cortex.store.update_step(task["id"], "inspect", "passed")
    recovery = cortex.recover(task["id"])
    assert recovery["recoverable"] is True
    assert recovery["next_steps"][0]["id"] == "sandbox"


def test_blocked_steps_cannot_be_finalized_or_reexecuted(tmp_path):
    cortex = make_cortex(tmp_path)
    task = cortex.create_task("Odpověz", intent="chat")
    for step in task["steps"]:
        cortex.store.update_step(task["id"], step["id"], "passed")
    cortex.store.update_step(task["id"], "execute", "blocked")
    cortex.add_evidence(task["id"], claim="Test passed", kind="test", source="test", verified=True, strength=1)
    assert cortex.finalize(task["id"])["status"] == "blocked"
    calls = []
    result = cortex.execute(task["id"], {"*": lambda *_: calls.append(True)})
    assert result["reason"] == "requires_reconciliation"
    assert calls == []


def test_step_attempt_limit_blocks_loop(tmp_path: Path) -> None:
    cortex = RavenCortex(CortexStore(tmp_path / "cortex.sqlite3"))
    task = cortex.create_task("Odpověz", intent="chat", complexity="standard")
    cortex.store.update_step(task["id"], "execute", "failed", increment_attempt=True)
    blocked = cortex.store.update_step(task["id"], "execute", "failed", increment_attempt=True)
    assert blocked["status"] == "blocked"
    blocked = cortex.store.update_step(task["id"], "execute", "failed", increment_attempt=True)
    assert blocked["status"] == "blocked"


def test_large_file_read_does_not_break_evidence_record(tmp_path: Path) -> None:
    cortex = make_cortex(tmp_path)
    task = cortex.create_task("Přečti soubor", intent="file")
    item = cortex.add_evidence(task["id"], claim="x" * 24000, kind="file", source="reader", verified=True)
    assert len(item["claim"]) == 1200


def test_role_executor_runs_dependencies_and_collects_real_evidence(tmp_path: Path) -> None:
    cortex = make_cortex(tmp_path)
    task = cortex.create_task("Odpověz a ověř výsledek", intent="chat", complexity="standard")
    calls = []

    def handler(_task, step):
        calls.append(step["id"])
        operational = step["id"] in {"inspect", "verify"}
        return {
            "ok": True,
            "claim": f"Dokončen krok {step['id']}",
            "kind": "test" if operational else "model_response",
            "source": step["agent"],
            "verified": operational,
            "strength": 0.95 if operational else 0.5,
        }

    result = cortex.execute(task["id"], {"*": handler})
    assert result["status"] == "completed"
    assert calls == ["inspect", "execute", "verify", "review"]
    assert cortex.store.task(task["id"])["evidence"]


def test_role_executor_stops_high_risk_step_for_approval(tmp_path: Path) -> None:
    cortex = make_cortex(tmp_path)
    task = cortex.create_task("Publikuj aplikaci na GitHub", intent="system", complexity="complex")

    def handler(_task, step):
        return {"ok": True, "claim": step["title"], "kind": "test", "source": step["agent"], "verified": True}

    result = cortex.execute(task["id"], {"*": handler})
    assert result["status"] == "waiting_approval"
    assert result["step"]["id"] == "execute"
    resumed = cortex.execute(task["id"], {"*": handler}, approval=lambda _task, _step: True)
    assert resumed["status"] == "completed"


def test_role_executor_blocks_after_bounded_identical_failures(tmp_path: Path) -> None:
    cortex = make_cortex(tmp_path)
    task = cortex.create_task("Odpověz", intent="chat", complexity="standard")

    def handler(_task, step):
        if step["id"] == "execute":
            return {"ok": False, "claim": "Stejná chyba", "kind": "error", "source": "executor", "verified": True}
        return {"ok": True, "claim": step["title"], "kind": "test", "source": step["agent"], "verified": True}

    result = cortex.execute(task["id"], {"*": handler})
    assert result["status"] == "blocked"
    assert result["reason"] == "attempt_limit"
    assert result["step"]["attempts"] == 2


def test_production_operation_requires_dependencies_and_cannot_repeat(tmp_path):
    cortex = make_cortex(tmp_path)
    task = cortex.create_task("Přečti soubor", intent="file")
    calls = []
    def operation():
        calls.append(True)
        return {"status": "completed", "verified": True}
    with pytest.raises(ValueError, match="Závislosti"):
        cortex.run_operation(task["id"], "execute", operation)
    assert not calls
    cortex.store.update_step(task["id"], "inspect", "passed")
    assert cortex.run_operation(task["id"], "execute", operation)["verified"]
    with pytest.raises(ValueError, match="již byl spuštěn"):
        cortex.run_operation(task["id"], "execute", operation)
    assert calls == [True]


def test_production_operation_blocks_uncertain_failure(tmp_path):
    cortex = make_cortex(tmp_path)
    task = cortex.create_task("Přečti soubor", intent="file")
    cortex.store.update_step(task["id"], "inspect", "passed")
    def operation():
        raise OSError("interrupted")
    with pytest.raises(OSError):
        cortex.run_operation(task["id"], "execute", operation)
    assert cortex.store.task(task["id"])["status"] == "blocked"
    with pytest.raises(ValueError, match="již byl spuštěn"):
        cortex.run_operation(task["id"], "execute", operation)


def test_unverified_production_operation_blocks_whole_task(tmp_path):
    cortex = make_cortex(tmp_path)
    task = cortex.create_task("Proveď systémovou akci", intent="system")
    cortex.store.update_step(task["id"], "inspect", "passed")
    result = cortex.run_operation(task["id"], "execute", lambda: {"status": "unverified", "verified": False})
    assert result["verified"] is False
    persisted = cortex.store.task(task["id"])
    assert persisted["status"] == "blocked"
    assert next(step for step in persisted["steps"] if step["id"] == "execute")["status"] == "blocked"


def test_production_operation_confirmation_does_not_duplicate_work(tmp_path):
    cortex = make_cortex(tmp_path)
    task = cortex.create_task("Přečti soubor", intent="file")
    cortex.store.update_step(task["id"], "inspect", "passed")
    cortex.run_operation(task["id"], "execute", lambda: {"status": "confirmation_required"})
    assert cortex.run_operation(task["id"], "execute", lambda: {"status": "completed", "verified": True})["verified"]


def test_production_operation_is_atomic_across_store_instances(tmp_path):
    cortex = make_cortex(tmp_path)
    other = RavenCortex(CortexStore(cortex.store.path))
    task = cortex.create_task("Přečti soubor", intent="file")
    cortex.store.update_step(task["id"], "inspect", "passed")
    def run(engine):
        try:
            engine.run_operation(task["id"], "execute", lambda: {"status": "completed", "verified": True})
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, [cortex, other])) == [False, True]

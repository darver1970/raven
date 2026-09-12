from pathlib import Path

import pytest
import raven_learning

from raven_learning import LearningStore, sanitize_training_text


@pytest.fixture()
def learning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LearningStore:
    monkeypatch.setattr(raven_learning, "DATASET_DIR", tmp_path / "datasets")
    return LearningStore(tmp_path / "learning.sqlite3")


def test_training_text_redacts_secrets_and_personal_data() -> None:
    text, redactions = sanitize_training_text("api_key=abc123 email test@example.com telefon 777 123 456")
    assert "abc123" not in text
    assert "test@example.com" not in text
    assert "777 123 456" not in text
    assert redactions == ["personal", "secret"]


def test_feedback_requires_explicit_training_consent(learning: LearningStore) -> None:
    entry = learning.add_feedback({"prompt": "Kolik je 2+2?", "answer": "4", "rating": 1})
    assert entry["approved_for_training"] is False
    exported = learning.export_dataset("test")
    assert exported["examples"] == 0


def test_approved_feedback_exports_chat_jsonl(learning: LearningStore) -> None:
    learning.add_feedback({"prompt": "Pozdrav", "answer": "Ahoj", "rating": 1, "approved_for_training": True})
    exported = learning.export_dataset("approved")
    assert exported["examples"] == 1
    assert Path(exported["path"]).read_text(encoding="utf-8").count("\n") == 1
    assert exported["redaction_applied"] is True
    assert exported["requires_manual_privacy_review"] is True


def test_expired_memory_is_not_used(learning: LearningStore) -> None:
    learning.remember({"title": "Starý stav", "content": "Neplatné", "valid_until": "2020-01-01T00:00:00Z"})
    assert learning.memories() == []


def test_feedback_can_be_forgotten_with_its_lesson(learning: LearningStore) -> None:
    record = learning.add_feedback({"prompt": "Pozdrav", "answer": "Ahoj", "rating": 1})
    assert learning.memories()
    assert learning.forget_feedback(record["id"])["deleted"] is True
    assert learning.feedback_list() == []
    assert learning.memories() == []


def test_correction_becomes_high_confidence_lesson(learning: LearningStore) -> None:
    learning.add_feedback({"prompt": "Hlavní město ČR", "answer": "Brno", "corrected_answer": "Praha", "rating": -1})
    memories = learning.memories("hlavní město")
    assert memories[0]["content"] == "Praha"
    assert memories[0]["outcome"] == "corrected"


def test_feedback_for_same_chat_message_is_replaced(learning: LearningStore) -> None:
    first = learning.add_feedback({
        "chat_id": "chat-a", "task_id": "a" * 32,
        "prompt": "Je to správně?", "answer": "Ano", "rating": 1,
    })
    second = learning.add_feedback({
        "chat_id": "chat-a", "task_id": "a" * 32,
        "prompt": "Je to správně?", "answer": "Ano", "rating": -1,
        "corrected_answer": "Ne",
    })
    assert first["id"] != second["id"]
    feedback = learning.feedback_list()
    assert len(feedback) == 1
    assert feedback[0]["rating"] == -1
    memories = learning.memories("správně")
    assert len(memories) == 1
    assert memories[0]["content"] == "Ne"


def test_knowledge_graph_links_nodes(learning: LearningStore) -> None:
    relation = learning.add_knowledge({"type": "project", "label": "Raven"}, "uses", {"type": "component", "label": "Cortex"}, "source code", 0.95)
    graph = learning.graph()
    assert relation["relation"] == "uses"
    assert len(graph["nodes"]) == 2
    assert len(graph["edges"]) == 1


def test_eval_status_tracks_pass_and_failure(learning: LearningStore) -> None:
    learning.record_eval("core", "model-a", "shadow", 0.9, {"accuracy": 0.9})
    learning.record_eval("core", "model-b", "shadow", 0.4, {"accuracy": 0.4})
    status = learning.status()
    assert status["evaluations"]["total"] == 2
    assert status["evaluations"]["passed"] == 1

"""Lokální učící vrstva Ravenu: zpětná vazba, paměť, graf a tréninková data.

Soukromá data nikdy nezařadí do datasetu automaticky. Tréninkový záznam vznikne
jen z výslovně potvrzené odpovědi a před uložením projde deterministickou redakcí.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


ROOT = Path(__file__).resolve().parent
LEARNING_DB_PATH = ROOT / "runtime" / "raven-learning.sqlite3"
DATASET_DIR = ROOT / "runtime" / "training" / "datasets"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_ -]?key|token|secret|password|heslo)\b\s*[:=]\s*[^\s,;]+"),
    re.compile(r"\b(?:sk|ghp|github_pat|xox[baprs])[-_][A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)authorization:\s*bearer\s+\S+"),
)
PERSONAL_PATTERNS = (
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?<!\d)(?:\+?420\s*)?(?:\d[ -]?){9}(?!\d)"),
)


def sanitize_training_text(value: Any) -> tuple[str, list[str]]:
    text = str(value or "")[:200_000]
    redactions: list[str] = []
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            redactions.append("secret")
            text = pattern.sub("[REDACTED_SECRET]", text)
    for pattern in PERSONAL_PATTERNS:
        if pattern.search(text):
            redactions.append("personal")
            text = pattern.sub("[REDACTED_PERSONAL]", text)
    text = re.sub(r"(?i)\b[A-Z]:\\Users\\[^\\\s]+", r"C:\\Users\\[USER]", text)
    return text.strip(), sorted(set(redactions))


class LearningStore:
    def __init__(self, path: Path = LEARNING_DB_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._lock, self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY, chat_id TEXT NOT NULL DEFAULT '', task_id TEXT NOT NULL DEFAULT '',
                    prompt TEXT NOT NULL, answer TEXT NOT NULL, corrected_answer TEXT NOT NULL DEFAULT '',
                    rating INTEGER NOT NULL, tags_json TEXT NOT NULL, approved_for_training INTEGER NOT NULL DEFAULT 0,
                    redactions_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS episodes (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
                    content TEXT NOT NULL, cause TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL, source TEXT NOT NULL, valid_from TEXT NOT NULL,
                    valid_until TEXT NOT NULL DEFAULT '', superseded_by TEXT NOT NULL DEFAULT '', fingerprint TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_episodes_scope ON episodes(scope,kind,valid_until);
                CREATE TABLE IF NOT EXISTS knowledge_nodes (
                    id TEXT PRIMARY KEY, type TEXT NOT NULL, label TEXT NOT NULL, data_json TEXT NOT NULL,
                    confidence REAL NOT NULL, updated_at TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS knowledge_edges (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL, relation TEXT NOT NULL, target_id TEXT NOT NULL,
                    evidence TEXT NOT NULL, confidence REAL NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(source_id,relation,target_id),
                    FOREIGN KEY(source_id) REFERENCES knowledge_nodes(id), FOREIGN KEY(target_id) REFERENCES knowledge_nodes(id)
                );
                CREATE TABLE IF NOT EXISTS eval_results (
                    id TEXT PRIMARY KEY, suite TEXT NOT NULL, model TEXT NOT NULL, mode TEXT NOT NULL,
                    score REAL NOT NULL, passed INTEGER NOT NULL, metrics_json TEXT NOT NULL,
                    baseline_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS brain_snapshots (
                    id TEXT PRIMARY KEY, version TEXT NOT NULL, manifest_json TEXT NOT NULL,
                    stable INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
                );
            """)

    def add_feedback(self, data: dict[str, Any]) -> dict[str, Any]:
        rating = int(data.get("rating", 0))
        if rating not in {-1, 1}:
            raise ValueError("Hodnocení musí být 1 nebo -1.")
        prompt, prompt_redactions = sanitize_training_text(data.get("prompt"))
        answer, answer_redactions = sanitize_training_text(data.get("answer"))
        corrected, correction_redactions = sanitize_training_text(data.get("corrected_answer"))
        if not prompt or not answer:
            raise ValueError("Zpětná vazba potřebuje otázku a odpověď.")
        approved = data.get("approved_for_training") is True
        entry = {
            "id": uuid4().hex, "chat_id": str(data.get("chat_id", ""))[:128],
            "task_id": str(data.get("task_id", ""))[:32], "prompt": prompt, "answer": answer,
            "corrected_answer": corrected, "rating": rating,
            "tags": sorted({str(item)[:48] for item in data.get("tags", []) if str(item).strip()})[:20],
            "approved_for_training": approved,
            "redactions": sorted(set(prompt_redactions + answer_redactions + correction_redactions)),
            "created_at": _now(),
        }
        with self._lock, self.connect() as db:
            # Jedna odpověď má právě jedno aktuální hodnocení. Opakované kliknutí
            # nebo změna palce tedy záznam nahradí, místo aby vyráběla duplicity.
            if entry["chat_id"] and entry["task_id"]:
                previous = db.execute(
                    "SELECT id FROM feedback WHERE chat_id=? AND task_id=?",
                    (entry["chat_id"], entry["task_id"]),
                ).fetchall()
                for row in previous:
                    db.execute("DELETE FROM episodes WHERE source=?", ("feedback:" + row["id"],))
                db.execute(
                    "DELETE FROM feedback WHERE chat_id=? AND task_id=?",
                    (entry["chat_id"], entry["task_id"]),
                )
            db.execute("INSERT INTO feedback VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                entry["id"], entry["chat_id"], entry["task_id"], prompt, answer, corrected, rating,
                json.dumps(entry["tags"], ensure_ascii=False), int(approved),
                json.dumps(entry["redactions"], ensure_ascii=False), entry["created_at"],
            ))
        if rating > 0 or corrected:
            self.remember({
                "scope": "project", "kind": "lesson", "title": prompt[:120],
                "content": corrected or answer, "cause": prompt, "outcome": "accepted" if rating > 0 else "corrected",
                "confidence": 0.85 if rating > 0 else 0.95, "source": "feedback:" + entry["id"],
            })
        return entry

    def forget_feedback(self, feedback_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-f0-9]{32}", feedback_id):
            raise ValueError("Neplatné ID zpětné vazby.")
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                deleted = db.execute("DELETE FROM feedback WHERE id=?", (feedback_id,)).rowcount
                db.execute("DELETE FROM episodes WHERE source=?", ("feedback:" + feedback_id,))
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise
        return {"deleted": bool(deleted), "id": feedback_id,
                "notice": "Již exportované datasety nejsou změněny; případně je smažte nebo znovu exportujte."}

    def feedback_list(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT id,substr(prompt,1,120) prompt,rating,approved_for_training,created_at FROM feedback ORDER BY created_at DESC LIMIT ?", (max(1, min(100, limit)),)).fetchall()
        return [dict(row) for row in rows]

    def remember(self, data: dict[str, Any]) -> dict[str, Any]:
        scope = str(data.get("scope", "project"))[:64]
        kind = str(data.get("kind", "fact"))[:48]
        title, _ = sanitize_training_text(data.get("title"))
        content, _ = sanitize_training_text(data.get("content"))
        if not title or not content:
            raise ValueError("Paměť potřebuje název a obsah.")
        fingerprint = _hash([scope, kind, title.lower(), content.lower()])
        entry = {
            "id": uuid4().hex, "scope": scope, "kind": kind, "title": title[:300], "content": content,
            "cause": str(data.get("cause", ""))[:4000], "outcome": str(data.get("outcome", ""))[:2000],
            "confidence": max(0.0, min(1.0, float(data.get("confidence", 0.7)))),
            "source": str(data.get("source", "user"))[:200], "valid_from": _now(),
            "valid_until": str(data.get("valid_until", ""))[:40], "fingerprint": fingerprint,
        }
        with self._lock, self.connect() as db:
            existing = db.execute("SELECT id FROM episodes WHERE fingerprint=? AND superseded_by=''", (fingerprint,)).fetchone()
            if existing:
                entry["id"] = existing["id"]
            else:
                db.execute("INSERT INTO episodes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    entry["id"], scope, kind, entry["title"], content, entry["cause"], entry["outcome"],
                    entry["confidence"], entry["source"], entry["valid_from"], entry["valid_until"], "", fingerprint,
                ))
        return entry

    def memories(self, query: str = "", scope: str = "", limit: int = 30) -> list[dict[str, Any]]:
        terms = {item for item in re.findall(r"[a-z0-9á-ž_-]{3,}", query.lower())}
        with self.connect() as db:
            rows = db.execute("SELECT * FROM episodes WHERE superseded_by='' AND (valid_until='' OR julianday(valid_until)>julianday('now')) AND (?='' OR scope=?) ORDER BY valid_from DESC LIMIT 500", (scope, scope)).fetchall()
        ranked = []
        for row in rows:
            item = dict(row)
            words = set(re.findall(r"[a-z0-9á-ž_-]{3,}", f"{item['title']} {item['content']} {item['cause']}".lower()))
            relevance = len(terms & words) / max(1, len(terms)) if terms else 1.0
            if terms and not relevance:
                continue
            item["score"] = round(relevance * 0.7 + float(item["confidence"]) * 0.3, 4)
            ranked.append(item)
        return sorted(ranked, key=lambda item: item["score"], reverse=True)[:max(1, min(100, limit))]

    def add_knowledge(self, source: dict[str, Any], relation: str, target: dict[str, Any], evidence: str = "", confidence: float = 0.7) -> dict[str, Any]:
        def node(value: dict[str, Any]) -> str:
            node_type = str(value.get("type", "concept"))[:48]
            label = str(value.get("label", "")).strip()[:300]
            if not label:
                raise ValueError("Uzel znalostí potřebuje název.")
            fingerprint = _hash([node_type, label.lower()])
            with self.connect() as db:
                old = db.execute("SELECT id FROM knowledge_nodes WHERE fingerprint=?", (fingerprint,)).fetchone()
                if old:
                    return str(old["id"])
                node_id = uuid4().hex
                db.execute("INSERT INTO knowledge_nodes VALUES(?,?,?,?,?,?,?)", (node_id, node_type, label, json.dumps(value.get("data", {}), ensure_ascii=False), max(0, min(1, confidence)), _now(), fingerprint))
                return node_id
        with self._lock:
            source_id, target_id = node(source), node(target)
            edge_id = uuid4().hex
            with self.connect() as db:
                db.execute("INSERT OR IGNORE INTO knowledge_edges VALUES(?,?,?,?,?,?,?)", (edge_id, source_id, str(relation)[:80], target_id, str(evidence)[:2000], max(0, min(1, confidence)), _now()))
        return {"source_id": source_id, "relation": str(relation)[:80], "target_id": target_id}

    def graph(self, query: str = "", limit: int = 100) -> dict[str, Any]:
        pattern = f"%{query[:200]}%"
        with self.connect() as db:
            nodes = db.execute("SELECT * FROM knowledge_nodes WHERE (?='' OR label LIKE ?) ORDER BY updated_at DESC LIMIT ?", (query, pattern, max(1, min(500, limit)))).fetchall()
            ids = [row["id"] for row in nodes]
            edges = []
            if ids:
                marks = ",".join("?" for _ in ids)
                edges = db.execute(f"SELECT * FROM knowledge_edges WHERE source_id IN ({marks}) OR target_id IN ({marks}) LIMIT 500", (*ids, *ids)).fetchall()
        return {"nodes": [dict(row) for row in nodes], "edges": [dict(row) for row in edges]}

    def export_dataset(self, name: str = "raven-approved") -> dict[str, Any]:
        safe_name = re.sub(r"[^a-z0-9._-]", "-", name.lower()).strip("-.")[:64] or "raven-approved"
        with self.connect() as db:
            rows = db.execute("SELECT * FROM feedback WHERE approved_for_training=1 ORDER BY created_at").fetchall()
        DATASET_DIR.mkdir(parents=True, exist_ok=True)
        path = DATASET_DIR / f"{safe_name}.jsonl"
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                chosen = row["corrected_answer"] or (row["answer"] if row["rating"] > 0 else "")
                if not chosen:
                    continue
                stream.write(json.dumps({"messages": [{"role": "user", "content": row["prompt"]}, {"role": "assistant", "content": chosen}], "source_id": row["id"]}, ensure_ascii=False) + "\n")
        temporary.replace(path)
        count = sum(1 for row in rows if row["corrected_answer"] or row["rating"] > 0)
        return {"path": str(path), "examples": count, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "format": "chat-jsonl", "redaction_applied": True, "requires_manual_privacy_review": True}

    def record_eval(self, suite: str, model: str, mode: str, score: float, metrics: dict[str, Any], baseline_id: str = "") -> dict[str, Any]:
        result = {"id": uuid4().hex, "suite": suite[:80], "model": model[:160], "mode": mode[:32], "score": max(0.0, min(1.0, score)), "passed": score >= 0.7, "metrics": metrics, "baseline_id": baseline_id[:32], "created_at": _now()}
        with self._lock, self.connect() as db:
            db.execute("INSERT INTO eval_results VALUES(?,?,?,?,?,?,?,?,?)", (result["id"], result["suite"], result["model"], result["mode"], result["score"], int(result["passed"]), json.dumps(metrics, ensure_ascii=False), result["baseline_id"], result["created_at"]))
        return result

    def status(self) -> dict[str, Any]:
        with self.connect() as db:
            feedback = db.execute("SELECT COUNT(*) total,SUM(approved_for_training) approved,SUM(rating=1) positive FROM feedback").fetchone()
            memory = db.execute("SELECT COUNT(*) total FROM episodes WHERE superseded_by=''").fetchone()
            graph = db.execute("SELECT (SELECT COUNT(*) FROM knowledge_nodes) nodes,(SELECT COUNT(*) FROM knowledge_edges) edges").fetchone()
            evaluations = db.execute("SELECT COUNT(*) total,SUM(passed) passed FROM eval_results").fetchone()
        return {"engine": "Raven Learning", "database": str(self.path), "wal": True, "feedback": dict(feedback), "memory": dict(memory), "knowledge_graph": dict(graph), "evaluations": dict(evaluations), "training_policy": "explicit-consent-only"}


LEARNING = LearningStore()

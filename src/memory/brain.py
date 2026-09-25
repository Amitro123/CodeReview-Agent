"""SQLite-backed agent memory: an LLM response cache plus a log of past analyses
that later runs can recall from.

Modeled on agent-brain's "cheap cache of accurate context": every run is recorded as a
structured lesson (root cause + fix), but recall returns at most MAX_RECALL short matches
from the same repo, so memory stays a small prompt addition instead of a growing
context tax.
"""
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

DEFAULT_DB_PATH = "~/.codereview-agent/brain.db"
MAX_RECALL = 2
LESSON_CHARS = 300
MAX_QUERY_TOKENS = 12

_STOPWORDS = {
    "the", "and", "for", "this", "that", "with", "why", "what", "how", "not", "does",
    "doesn", "isn", "are", "was", "from", "have", "has", "but", "can", "when", "there",
    "its", "into", "fix", "please", "page", "error",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cache (
    key TEXT PRIMARY KEY,
    response TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    kind TEXT NOT NULL,
    repo TEXT NOT NULL,
    query TEXT NOT NULL,
    error_signature TEXT NOT NULL,
    root_cause TEXT NOT NULL,
    fix_checklist TEXT NOT NULL,
    files TEXT NOT NULL,
    confidence TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS runs_fts USING fts5(
    query, error_signature, root_cause, content='runs', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS runs_ai AFTER INSERT ON runs BEGIN
    INSERT INTO runs_fts(rowid, query, error_signature, root_cause)
    VALUES (new.id, new.query, new.error_signature, new.root_cause);
END;
"""


def error_signature(network_errors: list, console_errors: list) -> str:
    """A short, stable text fingerprint of a page's errors, used for recall matching."""
    parts = [str(e.get("text", ""))[:120] for e in console_errors[:5]]
    for e in network_errors[:5]:
        url = str(e.get("url", "")).split("?")[0]
        parts.append(f"{e.get('status')} {url.split('/', 3)[-1]}")
    return " | ".join(p for p in parts if p.strip())


def repo_fingerprint(repo_root: Optional[Path]) -> Optional[str]:
    """HEAD sha plus a hash of uncommitted changes, so cached code analysis is invalidated
    as soon as the code changes. None when the directory isn't a git checkout."""
    if repo_root is None:
        return None
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, timeout=5
        )
        if head.returncode != 0:
            return None
        diff = subprocess.run(
            ["git", "diff", "HEAD"], cwd=repo_root, capture_output=True, text=True, timeout=10
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    dirty = hashlib.sha256((diff.stdout + untracked.stdout).encode()).hexdigest()[:16]
    return f"{head.stdout.strip()}:{dirty}"


class Brain:
    def __init__(self, db_path: Optional[str] = None, cache_ttl_hours: Optional[float] = None):
        path = Path(db_path or os.getenv("AGENT_DB_PATH", DEFAULT_DB_PATH)).expanduser()
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), timeout=5, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        if cache_ttl_hours is None:
            cache_ttl_hours = float(os.getenv("LLM_CACHE_TTL_HOURS", "24"))
        self._ttl_seconds = cache_ttl_hours * 3600

    @staticmethod
    def cache_key(*parts: Any) -> str:
        return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()

    def cache_get(self, key: str) -> Optional[str]:
        if self._ttl_seconds <= 0:
            return None
        row = self._conn.execute(
            "SELECT response FROM llm_cache WHERE key = ? AND created_at >= ?",
            (key, time.time() - self._ttl_seconds),
        ).fetchone()
        return row["response"] if row else None

    def cache_set(self, key: str, response: str) -> None:
        if self._ttl_seconds <= 0:
            return
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO llm_cache (key, response, created_at) VALUES (?, ?, ?)",
                (key, response, time.time()),
            )

    def record_run(self, kind: str, repo: str, query: str, error_sig: str, root_cause: str,
                   fix_checklist: list, files: list, confidence: str) -> None:
        if not root_cause.strip():
            return
        with self._conn:
            # A cache hit re-produces the same answer; don't store it as a second lesson.
            duplicate = self._conn.execute(
                "SELECT 1 FROM runs WHERE repo = ? AND root_cause = ?", (repo or "", root_cause)
            ).fetchone()
            if duplicate:
                return
            self._conn.execute(
                "INSERT INTO runs (created_at, kind, repo, query, error_signature, root_cause,"
                " fix_checklist, files, confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), kind, repo or "", query or "", error_sig or "", root_cause,
                 json.dumps(fix_checklist), json.dumps(files), confidence or "low"),
            )

    def recall(self, repo: str, text: str, limit: int = MAX_RECALL) -> list[dict]:
        """Up to `limit` past runs on the same repo whose query/errors/root cause best match `text`."""
        tokens = []
        for word in re.findall(r"[a-z0-9_]{3,}", text.lower()):
            if word not in _STOPWORDS and word not in tokens:
                tokens.append(word)
        if not tokens:
            return []
        match = " OR ".join(f'"{t}"' for t in tokens[:MAX_QUERY_TOKENS])
        rows = self._conn.execute(
            "SELECT runs.* FROM runs_fts JOIN runs ON runs.id = runs_fts.rowid"
            " WHERE runs_fts MATCH ? AND runs.repo = ? ORDER BY bm25(runs_fts) LIMIT ?",
            (match, repo or "", min(limit, MAX_RECALL)),
        ).fetchall()
        return [
            {
                "query": r["query"],
                "root_cause": r["root_cause"],
                "fix_checklist": json.loads(r["fix_checklist"]),
                "files": json.loads(r["files"]),
            }
            for r in rows
        ]

    @staticmethod
    def format_lessons(lessons: list[dict]) -> str:
        """Compact one-paragraph-per-lesson text for a prompt; empty string when there are none."""
        lines = []
        for lesson in lessons:
            text = f"- Past issue \"{lesson['query']}\": root cause: {lesson['root_cause']}"
            if lesson["files"]:
                text += f" (files: {', '.join(lesson['files'][:5])})"
            lines.append(text[:LESSON_CHARS])
        return "\n".join(lines)

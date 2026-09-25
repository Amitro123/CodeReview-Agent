"""LLM response cache: one small JSON file per cached call, expired by age.

Deliberately not part of the knowledge base: these are raw model outputs keyed by a
hash of the request, useful only to the machine and safe to delete at any time.
"""
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

DEFAULT_CACHE_DIR = "~/.codereview-agent/cache"


class ResponseCache:
    def __init__(self, directory: Optional[str] = None, ttl_hours: Optional[float] = None):
        self.directory = Path(directory or os.getenv("LLM_CACHE_DIR", DEFAULT_CACHE_DIR)).expanduser()
        if ttl_hours is None:
            ttl_hours = float(os.getenv("LLM_CACHE_TTL_HOURS", "24"))
        self._ttl_seconds = ttl_hours * 3600

    @staticmethod
    def key(*parts: Any) -> str:
        return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> Optional[str]:
        if self._ttl_seconds <= 0:
            return None
        try:
            entry = json.loads(self._path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if time.time() - entry.get("created_at", 0) > self._ttl_seconds:
            return None
        return entry.get("response")

    def set(self, key: str, response: str) -> None:
        if self._ttl_seconds <= 0:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a concurrent reader never sees a half-written file.
        tmp = self._path(key).with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"created_at": time.time(), "response": response}), encoding="utf-8")
        tmp.replace(self._path(key))


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

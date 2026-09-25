"""MCP server exposing read-only repo tools (list/read/search files), sandboxed to one root directory.

Run standalone as a subprocess over stdio - see repo_client.py for the client side.
The root directory is taken from the MCP_REPO_ROOT env var so the client controls
exactly which directory this process is allowed to touch.
"""
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

REPO_ROOT = Path(os.environ.get("MCP_REPO_ROOT", ".")).resolve()

EXCLUDED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
# Every tool result is re-sent on each later turn of the loop, so keep file reads modest.
MAX_FILE_CHARS = 20_000
MAX_SEARCH_FILE_BYTES = 2_000_000
MAX_LIST_RESULTS = 300
MAX_SEARCH_RESULTS = 30

server = MCPServer("codereview-repo-tools")


def _resolve_safe(path: str) -> Path:
    """Resolves `path` relative to REPO_ROOT and rejects anything that escapes it."""
    candidate = (REPO_ROOT / path).resolve()
    if candidate != REPO_ROOT and REPO_ROOT not in candidate.parents:
        raise ValueError(f"path '{path}' is outside the repository root")
    return candidate


def _iter_repo_files():
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for filename in filenames:
            yield Path(dirpath) / filename


@server.tool()
def list_files(pattern: str = "**/*") -> list[str]:
    """Lists files in the repository matching a glob pattern (e.g. '**/*.py', 'src/**/*.js')."""
    matches = []
    for path in REPO_ROOT.glob(pattern):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        matches.append(str(path.relative_to(REPO_ROOT)))
        if len(matches) >= MAX_LIST_RESULTS:
            break
    return matches


@server.tool()
def read_file(path: str) -> str:
    """Reads a text file's contents from the repository. `path` is relative to the repo root."""
    try:
        resolved = _resolve_safe(path)
    except ValueError as e:
        return f"Error: {e}"
    if not resolved.is_file():
        return f"Error: '{path}' is not a file"
    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"Error reading '{path}': {e}"
    if len(content) > MAX_FILE_CHARS:
        content = content[:MAX_FILE_CHARS] + f"\n... [truncated, {len(content) - MAX_FILE_CHARS} more characters]"
    return content


@server.tool()
def search_code(query: str) -> list[str]:
    """Searches text files in the repository for a substring (case-insensitive). Returns 'path:line: content' entries."""
    results = []
    needle = query.lower()
    for path in _iter_repo_files():
        try:
            if path.stat().st_size > MAX_SEARCH_FILE_BYTES:
                continue
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for lineno, line in enumerate(f, start=1):
                    if needle in line.lower():
                        rel = path.relative_to(REPO_ROOT)
                        results.append(f"{rel}:{lineno}: {line.strip()[:200]}")
                        if len(results) >= MAX_SEARCH_RESULTS:
                            return results
        except OSError:
            continue
    return results


if __name__ == "__main__":
    server.run(transport="stdio")

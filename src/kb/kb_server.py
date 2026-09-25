"""MCP server over one project's knowledge base:

  query_kb(question)  - keyword search over page titles/summaries/tags/bodies; returns
                        the matching pages and the full content of the top match
  get_page(slug)      - the full content of one page, e.g. "issues/overlay-blocks-clicks"

The code agent spawns it during analysis. To give an IDE agent (Claude Code, Cursor) the
same knowledge, register it in the analyzed project's .mcp.json:

  {"mcpServers": {"codereview-kb": {
      "command": "python3",
      "args": ["/path/to/CodeReview-Agent/src/kb/kb_server.py"],
      "env": {"MCP_KB_ROOT": "/path/to/project/.codereview-kb"}}}}
"""
import os
import sys
from pathlib import Path

if __package__ in (None, ""):  # run as a script: make `src` importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from src.kb.graph import load_graph, search_pages  # noqa: E402
from src.kb.pages import normalize_slug, strip_frontmatter  # noqa: E402

KB_ROOT = Path(os.environ.get("MCP_KB_ROOT", ".codereview-kb")).resolve()
WIKI_DIR = KB_ROOT / "wiki"
GRAPH_FILE = KB_ROOT / "graph" / "knowledge-graph.json"

server = MCPServer(
    "codereview-kb",
    instructions=(
        "Knowledge base of past CodeReview Agent analyses of this project whose fixes the user "
        "verified. Use query_kb to search by symptom, component or error; get_page to read a page."
    ),
)


@server.tool()
def query_kb(question: str) -> str:
    """Search the project wiki (components, recurring issues, practices that worked) by
    keywords. Returns matching pages and the full content of the best match."""
    if not question.strip():
        return "Error: 'question' cannot be empty."
    matches = search_pages(question, load_graph(GRAPH_FILE), WIKI_DIR)
    if not matches:
        return f"No wiki pages match '{question}'. Nothing is known about this yet."
    lines = ["Relevant wiki pages:"]
    lines += [f"- {m['id']} - {m['title']} | {m['summary']}" for m in matches]
    top = WIKI_DIR / f"{matches[0]['id']}.md"
    if top.exists():
        lines += ["", f"--- Full content: {matches[0]['id']} ---", strip_frontmatter(top.read_text(encoding="utf-8"))]
    return "\n".join(lines)


@server.tool()
def get_page(slug: str) -> str:
    """Full content of one wiki page by slug, e.g. 'issues/overlay-blocks-clicks' or
    'components/login-form'. Use query_kb first if you don't know the slug."""
    normalized = normalize_slug(slug or "")
    path = WIKI_DIR / f"{normalized}.md" if normalized else None
    if path is None or not path.exists():
        name = (slug or "").rsplit("/", 1)[-1]
        similar = [n["id"] for n in load_graph(GRAPH_FILE)["nodes"] if name and name in n["id"]]
        return f"Page not found: '{slug}'." + (f" Similar: {', '.join(similar)}" if similar else "")
    return path.read_text(encoding="utf-8")


if __name__ == "__main__":
    server.run(transport="stdio")

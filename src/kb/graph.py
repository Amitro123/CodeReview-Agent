"""Deterministic knowledge graph over the wiki - no LLM, no database, no embeddings.

Pages are nodes, each page's `related_pages` frontmatter gives the edges, index.md gives
the summaries. The graph is regenerated after every ingest and read by the KB MCP server
and by recall.
"""
import json
import re
from datetime import date
from pathlib import Path

from src.kb.pages import WIKI_TYPES, normalize_slug, parse_frontmatter, strip_frontmatter

INDEX_ENTRY_RE = re.compile(r"^-\s+\[([^\]]+)\]\(([^)]+)\)\s+[—–-]+\s+(.+)$")
_STOP = {
    "the", "a", "an", "is", "in", "of", "and", "or", "how", "what", "why", "does", "do", "i", "for",
    "to", "that", "this", "it", "be", "not", "with", "on", "when", "fix", "page",
    "האם", "מה", "למה", "איך", "של", "על", "עם", "או", "גם", "לא", "יש", "אין",
}


def parse_index_summaries(index_path: Path) -> dict[str, str]:
    summaries: dict[str, str] = {}
    if index_path.exists():
        for line in index_path.read_text(encoding="utf-8").splitlines():
            m = INDEX_ENTRY_RE.match(line.strip())
            if m and (slug := normalize_slug(m.group(2))):
                summaries[slug] = m.group(3).strip()
    return summaries


def build_graph(wiki_dir: Path) -> dict:
    summaries = parse_index_summaries(wiki_dir / "index.md")
    nodes, related = [], {}
    for directory in WIKI_TYPES:
        for path in sorted((wiki_dir / directory).glob("*.md")):
            slug = f"{directory}/{path.stem}"
            fm = parse_frontmatter(path.read_text(encoding="utf-8"))
            nodes.append({
                "id": slug,
                "type": WIKI_TYPES[directory],
                "title": fm.get("title") or path.stem.replace("-", " ").title(),
                "summary": summaries.get(slug, ""),
                "tags": fm.get("tags", []),
                "run_ids": fm.get("run_ids", []),
            })
            related[slug] = fm.get("related_pages", [])

    ids = {n["id"] for n in nodes}
    edges, seen = [], set()
    for source, targets in related.items():
        for raw in targets:
            target = normalize_slug(raw)
            if target and target != source and target in ids and (source, target) not in seen:
                seen.add((source, target))
                edges.append({"from": source, "to": target, "relation": "related"})

    inbound = {n["id"]: 0 for n in nodes}
    for e in edges:
        inbound[e["to"]] += 1
    type_counts: dict[str, int] = {}
    for n in nodes:
        n["inbound_links"] = inbound[n["id"]]
        type_counts[n["type"]] = type_counts.get(n["type"], 0) + 1

    return {
        "meta": {
            "generated_at": date.today().isoformat(),
            "node_count": len(nodes),
            "edge_count": len(edges),
            **{f"{t}_count": c for t, c in sorted(type_counts.items())},
        },
        "nodes": sorted(nodes, key=lambda n: n["id"]),
        "edges": sorted(edges, key=lambda e: (e["from"], e["to"])),
    }


def write_graph(wiki_dir: Path, graph_path: Path) -> dict:
    graph = build_graph(wiki_dir)
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return graph


def load_graph(graph_path: Path) -> dict:
    if graph_path.exists():
        return json.loads(graph_path.read_text(encoding="utf-8"))
    return {"meta": {}, "nodes": [], "edges": []}


def search_pages(question: str, graph: dict, wiki_dir: Path, max_results: int = 5) -> list[dict]:
    """Keyword search over title, summary, tags and page body. Matching is substring in both directions, so a Hebrew query word
    with an attached prefix (ב-, ל-, ה-...) still matches the bare word."""
    keywords = {w for w in re.findall(r"\w+", question.lower()) if len(w) >= 2} - _STOP
    if not keywords:
        return []
    scored = []
    for node in graph.get("nodes", []):
        haystack = f"{node.get('title', '')} {node.get('summary', '')} {' '.join(node.get('tags', []))}".lower()
        path = wiki_dir / f"{node['id']}.md"
        if path.exists():
            haystack += " " + strip_frontmatter(path.read_text(encoding="utf-8")).lower()
        words = {w for w in re.findall(r"\w+", haystack) if len(w) >= 2}
        hits = sum(1 for kw in keywords if any(kw in w or w in kw for w in words if len(w) >= 3 or w == kw))
        if hits:
            scored.append((hits, node["inbound_links"], node))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    return [node for _, _, node in scored[:max_results]]


def summarize(graph: dict, relevant: list[dict], limit: int = 12) -> str:
    """Compact map of the wiki for a prompt: the most relevant pages first, then the best-connected ones, one line each."""
    if not graph.get("nodes"):
        return ""
    ordered = list(relevant)
    for node in sorted(graph["nodes"], key=lambda n: -n["inbound_links"]):
        if node not in ordered:
            ordered.append(node)
    meta = graph["meta"]
    lines = [f"Project wiki: {meta.get('node_count', 0)} pages, {meta.get('edge_count', 0)} links."]
    for node in ordered[:limit]:
        lines.append(f"  [{node['type'][0].upper()}] {node['id']} - {node['summary'][:100]}")
    if len(ordered) > limit:
        lines.append(f"  ... {len(ordered) - limit} more (use query_kb to search)")
    return "\n".join(lines)

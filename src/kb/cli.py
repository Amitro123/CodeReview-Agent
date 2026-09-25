"""Maintenance commands for a project knowledge base (no LLM calls):

  python -m src.kb.cli graph <kb_dir>                 regenerate graph/knowledge-graph.json
  python -m src.kb.cli lint  <kb_dir> [--repo <dir>]  report structural issues; exit 1 if any
  python -m src.kb.cli calibration <kb_dir>           routing accuracy per confidence bucket

<kb_dir> is e.g. <project>/.codereview-kb. With --repo, lint also flags pages that name
files which no longer exist in that checkout.
"""
import argparse
import sys
from pathlib import Path

from src.kb.graph import write_graph
from src.kb.wiki import KnowledgeBase


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.kb.cli")
    parser.add_argument("command", choices=["graph", "lint", "calibration"])
    parser.add_argument("kb_dir", type=Path)
    parser.add_argument("--repo", type=Path, default=None)
    args = parser.parse_args(argv)

    kb = KnowledgeBase(args.kb_dir.resolve(), args.kb_dir.resolve().name, args.repo.resolve() if args.repo else None)
    if args.command == "calibration":
        report = kb.calibration()
        if not report:
            print("No routed runs with an outcome yet (a 👍, or a re-route to another category).")
            return 0
        print(f"{'confidence':<12}{'runs':>6}{'accuracy':>10}{'mean conf.':>12}  methods")
        for row in report:
            methods = ", ".join(f"{m}: {n}" for m, n in row["methods"].items())
            print(f"{row['range']:<12}{row['count']:>6}{row['accuracy']:>10.0%}{row['mean_confidence']:>12.0%}  {methods}")
        return 0
    if not kb.wiki_dir.exists():
        print(f"No wiki at {kb.wiki_dir}", file=sys.stderr)
        return 1
    if args.command == "graph":
        meta = write_graph(kb.wiki_dir, kb.graph_path)["meta"]
        print(f"wrote {kb.graph_path} ({meta['node_count']} pages, {meta['edge_count']} links)")
        return 0
    issues = kb.lint()
    for issue in issues:
        print(f"- {issue}")
    print(f"{len(issues)} issue(s)")
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

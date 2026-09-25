"""Wiki page format: YAML frontmatter + markdown body. `related_pages` drive the graph,
index.md holds the one-line summaries.

Frontmatter is always rendered here, never written by the LLM, so every page stays
parseable by the graph generator and the linter.
"""
import json
import re
from datetime import date
from pathlib import Path
from typing import Optional

WIKI_TYPES = {"components": "component", "issues": "issue", "practices": "practice"}
SLUG_RE = re.compile(r"^(components|issues|practices)/[a-z0-9]+(?:-[a-z0-9]+)*$")

_FRONTMATTER = re.compile(r"^(?:<!--.*?-->\s*\n)?---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_LIST_FIELDS = ("run_ids", "related_pages", "tags")

TEMPLATE = """<!--
Page template for the CodeReview Agent project wiki. The agent writes the body; the
frontmatter is rendered by src/kb/pages.py. Humans may edit either.
-->
---
title: ""
wiki_type: "issue"      # component | issue | practice (matches the directory)
last_updated: "YYYY-MM-DD"
run_ids: []             # every run whose verdict this page relies on
related_pages: []       # e.g. "components/login-form" - these become graph edges
tags: []                # keywords used for search and routing
---

# Summary

One or two sentences: what this is and what to know first.

# Facts

Verified observations, each ending with its source: (run <run_id>, <YYYY-MM-DD>).
Name real files in backticks, e.g. `src/components/Login.tsx`.

# What worked

Fixes the user confirmed (👍), with the run id.

# What didn't work

Fixes the user rejected (👎), with the run id and, when known, the actual cause.
A failed fix is a lesson too: record it here so it isn't suggested again.

# Open questions

Anything unresolved. Never phrase an open question as a fact.

<!-- Contradictions: never delete an old claim. Strike it through and correct it in place:
~~old claim~~ (superseded <YYYY-MM-DD> by run <run_id>: <why>) -->
"""


def parse_frontmatter(text: str) -> dict:
    """Scalar and list fields of the frontmatter block ({} if there is none)."""
    m = _FRONTMATTER.match(text)
    if not m:
        return {}
    out: dict = {}
    block = m.group(1)
    for line in block.splitlines():
        if ":" in line and not line.strip().startswith("-"):
            key, _, value = line.partition(":")
            value = value.strip()
            if value.startswith('"'):
                end = value.find('"', 1)
                value = value[1:end] if end > 0 else value[1:]
            elif not value.startswith("["):
                value = value.split("#", 1)[0].strip()
            out[key.strip()] = value
    for field in _LIST_FIELDS:
        out[field] = _parse_list(block, field)
    return out


def _parse_list(block: str, field: str) -> list[str]:
    """Inline (`field: ["a", "b"]`) or multi-line (`field:` then `- a`) YAML list."""
    inline = re.search(rf"^{re.escape(field)}\s*:\s*\[([^\]]*)\]", block, re.MULTILINE)
    if inline:
        return [v.strip().strip('"').strip("'") for v in inline.group(1).split(",") if v.strip().strip('"').strip("'")]
    values, in_field = [], False
    for line in block.splitlines():
        if re.match(rf"\s*{re.escape(field)}\s*:", line):
            in_field = True
            continue
        if in_field:
            stripped = line.strip()
            if stripped.startswith("-"):
                value = stripped[1:].strip().strip('"').strip("'")
                if value:
                    values.append(value)
            elif stripped and not stripped.startswith("#"):
                break
    return values


def strip_frontmatter(text: str) -> str:
    m = _FRONTMATTER.match(text)
    return text[m.end():].lstrip("\n") if m else text


def normalize_slug(value: str) -> Optional[str]:
    """'wiki/issues/Foo Bar.md' -> 'issues/foo-bar'; None unless it's <type-dir>/<kebab-name>."""
    value = value.strip().removeprefix("wiki/")
    value = value[:-3] if value.endswith(".md") else value
    if "/" not in value:
        return None
    directory, _, name = value.partition("/")
    slug = f"{directory.lower()}/{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:60]}"
    return slug if SLUG_RE.match(slug) else None


def render_page(title: str, slug: str, body: str, run_ids: list[str], related_pages: list[str],
                tags: list[str], updated: Optional[date] = None) -> str:
    def yaml_list(values: list[str]) -> str:
        return json.dumps(sorted(dict.fromkeys(values)), ensure_ascii=False)

    title = " ".join(title.split()).replace('"', "'")
    return (
        "---\n"
        f'title: "{title}"\n'
        f'wiki_type: "{WIKI_TYPES[slug.split("/")[0]]}"\n'
        f'last_updated: "{(updated or date.today()).isoformat()}"\n'
        f"run_ids: {yaml_list(run_ids)}\n"
        f"related_pages: {yaml_list([p for p in related_pages if p != slug])}\n"
        f"tags: {yaml_list([t.lower() for t in tags])}\n"
        "---\n\n"
        f"{strip_frontmatter(body).strip()}\n"
    )


def page_path(wiki_dir: Path, slug: str) -> Path:
    """Path of a page, refusing anything that isn't a valid slug inside wiki_dir."""
    if not SLUG_RE.match(slug):
        raise ValueError(f"invalid page slug: {slug!r}")
    return wiki_dir / f"{slug}.md"

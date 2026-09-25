"""MISTAKES.md entries in agent-brain's format (see agent-brain-cursor's format-spec.md),
so agent-brain's `route`/`promote` can pick up what CodeReview Agent learned.

Entries are built from the run and the user's verdict with fixed templates - no LLM call.
"""
from datetime import datetime
from pathlib import Path

MISTAKES_HEADER = (
    "# MISTAKES\n\n"
    "Append-only inbox in agent-brain format. CodeReview Agent appends an entry whenever you\n"
    "say whether one of its fixes worked. Don't brief from this file; route it into PARA.\n"
)


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_entry(run: dict, worked: bool, note: str, when: datetime) -> str:
    query = _clip(run.get("query") or "(no query)", 80)
    root_cause = _clip(run.get("root_cause") or "(no root cause given)", 300)
    checklist = run.get("fix_checklist") or []
    first_step = _clip(checklist[0], 200) if checklist else "(no fix steps given)"
    files = ", ".join(f"`{f}`" for f in (run.get("files") or [])[:5])
    note = _clip(note, 400) if note else ""
    provenance = f"(CodeReview Agent run `{run['run_id']}` on `{run.get('project', '')}`)"

    lines = [f"## [{when:%Y-%m-%d %H:%M}] CodeReview fix {'worked' if worked else 'did not work'}: {query}"]
    if worked:
        lines += [
            "- **Type:** success",
            f"- **What happened:** For \"{query}\" the agent diagnosed: {root_cause}. The user confirmed the fix worked."
            + (f" User note: {note}" if note else "") + f" {provenance}",
            f"- **Success pattern:** {root_cause}" + (f" (in {files})" if files else ""),
            f"- **Impact:** \"{query}\" was fixed on the first suggested plan.",
            f"- **Repeat guidance:** When this symptom recurs, check this cause first: {first_step}",
        ]
    else:
        lines += [
            "- **Type:** mistake",
            f"- **What happened:** For \"{query}\" the agent diagnosed: {root_cause}. The user reported the fix did not work."
            + f" {provenance}",
            "- **Root cause:** "
            + (f"Per the user: {note}" if note else "The agent's diagnosis was wrong or incomplete; the actual cause is not recorded yet."),
            f"- **Consequence:** The suggested fix ({first_step}) did not resolve \"{query}\".",
            f"- **Prevention rule:** Don't conclude \"{_clip(root_cause, 120)}\" for this symptom without verifying it in the code first.",
        ]
    lines += ["- **PARA route:** unrouted", "- **Status:** unrouted"]
    return "\n".join(lines) + "\n"


def append_entry(path: Path, entry: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(MISTAKES_HEADER, encoding="utf-8")
    existing = path.read_text(encoding="utf-8")
    separator = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    with path.open("a", encoding="utf-8") as f:
        f.write(separator + entry)

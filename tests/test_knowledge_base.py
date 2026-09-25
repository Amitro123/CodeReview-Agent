import asyncio
import json
import re

import pytest

import src.main as main
from src import config
from src.agents.llm import LLM
from src.agents.mcp_tools import KB_SERVER, ToolBox
from src.kb import cli
from src.kb.cache import ResponseCache
from src.kb.pages import parse_frontmatter
from src.kb.wiki import KnowledgeBase, project_key, project_slug
from tests.fakes import ScriptedGroq

RUN = {"root_cause": "Modal overlay stays mounted after close", "fix_checklist": ["Unmount the overlay"],
       "files": ["src/Modal.tsx"], "confidence": "high"}


def _kb(tmp_path, name="acme/app"):
    return KnowledgeBase(tmp_path / "kb" / project_slug(name), name)


def _page(slug, body="# Summary\n\nx", related=(), tags=(), title=None, summary="s"):
    return {"slug": slug, "title": title or slug, "summary": summary, "tags": list(tags),
            "related_pages": list(related), "body": body}


def _ingest(kb, tmp_path, run_id, pages, log="changed"):
    client = ScriptedGroq([{"json": {"pages": pages, "log": log}}])
    written = asyncio.run(kb.ingest(run_id, LLM(ResponseCache(str(tmp_path / "c"), 1), client), "model"))
    return written, client


def _verified_run(kb, worked=False, note="", query="login button does nothing"):
    run_id = kb.record_run("universal", query, RUN)
    kb.record_feedback(run_id, worked, note)
    return run_id


def test_feedback_writes_agent_brain_entries_once(tmp_path):
    kb = _kb(tmp_path)
    failed = kb.record_run("universal", "login button does nothing", RUN)
    assert kb.record_feedback(failed, worked=False, note="z-index bug in Modal.css")
    assert not kb.record_feedback(failed, worked=True)  # a second click is ignored
    worked = kb.record_run("universal", "header color wrong", RUN)
    assert kb.record_feedback(worked, worked=True)

    text = kb.mistakes_path.read_text()
    assert text.count("\n## [") == 2
    mistake, success = re.split(r"\n(?=## \[)", text)[1:]
    # agent-brain's format-spec: header, Type, type-specific fields, PARA route, Status.
    assert re.match(r"## \[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] CodeReview fix did not work: ", mistake)
    for field in ["Type:** mistake", "What happened:**", "Root cause:** Per the user: z-index bug in Modal.css",
                  "Consequence:**", "Prevention rule:**", "PARA route:** unrouted", "Status:** unrouted"]:
        assert f"- **{field}" in mistake
    for field in ["Type:** success", "Success pattern:**", "Impact:**", "Repeat guidance:**"]:
        assert f"- **{field}" in success
    assert failed in mistake  # provenance back to the raw run


def test_feedback_rejects_unknown_or_malicious_run_ids(tmp_path):
    kb = _kb(tmp_path)
    kb.record_run("universal", "q", RUN)
    for bad in ["../../etc/passwd", "20260101-000000-abcdef", ""]:
        with pytest.raises(ValueError):
            kb.record_feedback(bad, True)


def test_mistakes_go_to_the_projects_own_agent_brain_inbox(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "MISTAKES.md").write_text("# MISTAKES\n\nexisting entry\n")
    monkeypatch.setattr(config, "REPO_PATHS", config._parse_repo_paths(f"acme/app={repo}"))
    kb = KnowledgeBase.for_project("acme/app")
    kb.record_feedback(kb.record_run("universal", "q", RUN), worked=True)
    assert kb.mistakes_path == repo.resolve() / "MISTAKES.md"
    assert "existing entry" in kb.mistakes_path.read_text()
    assert "Type:** success" in kb.mistakes_path.read_text()


def test_kb_lives_in_the_checkout_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("KB_LOCATION", "repo")
    monkeypatch.setattr(config, "REPO_PATHS", config._parse_repo_paths(f"acme/app={tmp_path}"))
    assert KnowledgeBase.for_project("acme/app").base == tmp_path.resolve() / ".codereview-kb"
    assert KnowledgeBase.for_project("other/app").base.parent == tmp_path / "kb"  # unmapped -> KB_DIR


def test_ingest_requires_a_verdict(tmp_path):
    kb = _kb(tmp_path)
    run_id = kb.record_run("universal", "q", RUN)
    with pytest.raises(ValueError, match="no verdict"):
        asyncio.run(kb.ingest(run_id, LLM(ResponseCache(str(tmp_path / "c"), 1), ScriptedGroq([])), "m"))


def test_ingest_writes_pages_index_graph_and_log(tmp_path):
    kb = _kb(tmp_path)
    first = _verified_run(kb)
    written, client = _ingest(kb, tmp_path, first, [
        _page("issues/login-overlay", related=["components/modal"], tags=["Login"], title="Login overlay"),
        _page("components/modal", title="Modal"),
        _page("../../etc/passwd"),          # not a valid slug -> skipped
        _page("notes/whatever"),            # unknown page type -> skipped
        _page("issues/empty", body="   "),  # empty body -> skipped
    ])
    assert written == ["issues/login-overlay", "components/modal"]
    assert not (tmp_path / "etc").exists()
    fm = parse_frontmatter((kb.wiki_dir / "issues/login-overlay.md").read_text())
    assert fm["title"] == "Login overlay" and fm["wiki_type"] == "issue"
    assert fm["run_ids"] == [first] and fm["related_pages"] == ["components/modal"] and fm["tags"] == ["login"]

    graph = json.loads(kb.graph_path.read_text())
    assert graph["edges"] == [{"from": "issues/login-overlay", "to": "components/modal", "relation": "related"}]
    assert {n["id"]: n["inbound_links"] for n in graph["nodes"]} == {"components/modal": 1, "issues/login-overlay": 0}

    # The ingest prompt carries the template, the run and the verdict.
    prompt = client.prompt(0)
    assert "# What didn't work" in prompt and first in prompt and '"worked": false' in prompt

    # A second verified run updating the same page merges run ids and replaces its index line.
    second = _verified_run(kb, worked=True)
    _ingest(kb, tmp_path, second, [_page("issues/login-overlay", title="Login overlay", summary="updated")])
    assert parse_frontmatter((kb.wiki_dir / "issues/login-overlay.md").read_text())["run_ids"] == sorted([first, second])
    index = kb.index_path.read_text()
    assert index.count("(issues/login-overlay.md)") == 1 and "— updated" in index

    log = kb.log_path.read_text()
    assert log.index(f"ingested run {second}") < log.index(f"ingested run {first}")  # newest first


def test_recall_gives_a_capped_map_and_the_best_page(tmp_path):
    kb = _kb(tmp_path)
    assert kb.recall("anything") == ""  # empty wiki -> nothing added to the prompt
    run_id = _verified_run(kb)
    long_body = "# Summary\n\nlogin overlay\n" + "\n".join(f"line {i}" for i in range(200))
    _ingest(kb, tmp_path, run_id, [
        _page("issues/login-overlay", body=long_body, summary="login overlay blocks clicks"),
        _page("components/header", summary="site header colors"),
    ])
    block = kb.recall("the login button is covered by an overlay")
    assert block.index("issues/login-overlay") < block.index("components/header")  # relevance first
    assert "Best-matching page, issues/login-overlay" in block
    assert "line 199" not in block and "get_page('issues/login-overlay')" in block


def test_fingerprint_moves_on_verdict_and_ingest_but_not_on_a_new_run(tmp_path):
    kb = _kb(tmp_path)
    run_id = kb.record_run("universal", "q", RUN)
    before = kb.fingerprint()
    kb.record_run("universal", "another question", RUN)
    assert kb.fingerprint() == before
    kb.record_feedback(run_id, worked=False)
    after_verdict = kb.fingerprint()
    assert after_verdict != before
    _ingest(kb, tmp_path, run_id, [_page("issues/x")])
    assert kb.fingerprint() != after_verdict


def test_lint_reports_structural_problems(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "Modal.tsx").write_text("")
    kb = KnowledgeBase(tmp_path / "kb" / "p", "p", repo_root=repo)
    run_id = _verified_run(kb)
    _ingest(kb, tmp_path, run_id, [
        _page("issues/a", related=["components/missing"], body="Fix in `src/Modal.tsx` and `src/Gone.tsx`."),
    ])
    (kb.wiki_dir / "components" / "orphan.md").write_text("---\ntitle: \"Orphan\"\n---\n\nx\n")
    page = kb.wiki_dir / "issues" / "a.md"
    page.write_text(page.read_text().replace(f'"{run_id}"', '"20200101-000000-abcdef"'))

    issues = kb.lint()
    assert "components/orphan is not listed in index.md (orphan)" in issues
    assert "issues/a relates to missing page components/missing" in issues
    assert "issues/a cites run 20200101-000000-abcdef, which is not in raw/" in issues
    assert "issues/a mentions `src/Gone.tsx`, which no longer exists in the repo" in issues
    assert not any("Modal.tsx" in i for i in issues)
    assert cli.main(["lint", str(kb.base), "--repo", str(repo)]) == 1
    assert cli.main(["graph", str(kb.base)]) == 0


def test_kb_mcp_server_searches_and_reads_pages(tmp_path):
    kb = _kb(tmp_path)
    _ingest(kb, tmp_path, _verified_run(kb), [
        _page("issues/login-overlay", body="# Summary\n\nThe overlay covers the login button.",
              summary="login overlay blocks clicks", tags=["login"]),
    ])

    async def run():
        async with ToolBox([(KB_SERVER, {"MCP_KB_ROOT": str(kb.base)})]) as toolbox:
            return (
                await toolbox.call("query_kb", {"question": "why can't I click login"}),
                await toolbox.call("get_page", {"slug": "issues/login-overlay"}),
                await toolbox.call("get_page", {"slug": "../../../etc/passwd"}),
                await toolbox.call("query_kb", {"question": "database migration"}),
            )

    found, page, escape, nothing = asyncio.run(run())
    assert "issues/login-overlay" in found and "The overlay covers the login button." in found
    assert page.startswith("---\ntitle:")
    assert escape.startswith("Page not found")
    assert nothing.startswith("No wiki pages match")


def test_feedback_message_records_verdict_and_schedules_learning(monkeypatch, tmp_path):
    kb = KnowledgeBase.for_project("acme/app")
    run_id = kb.record_run("universal", "q", RUN)
    learned = []

    async def fake_learn(kb_arg, run_id_arg):
        learned.append(run_id_arg)

    monkeypatch.setattr(main, "learn_in_background", fake_learn)
    message = {"type": "feedback", "run_ref": {"run_id": run_id, "repo": "acme/app", "page_url": None},
               "worked": False, "note": "wrong file"}

    async def run():
        first = main.handle_feedback(message)
        second = main.handle_feedback(message)
        await asyncio.gather(*main._background_tasks)
        return first, second

    first, second = asyncio.run(run())
    assert first["type"] == "feedback_saved" and "duplicate" not in first
    assert second["duplicate"] is True
    assert learned == [run_id]
    assert "Per the user: wrong file" in kb.mistakes_path.read_text()


def test_project_identity():
    assert project_key("acme/app", "https://github.com/acme/app/actions") == "acme/app"
    assert project_key("dashboard/users", "http://localhost:3000/dashboard/users") == "localhost:3000"
    assert project_slug("acme/app") != project_slug("acme-app")
    assert re.match(r"^[a-z0-9_-]+$", project_slug("localhost:3000"))

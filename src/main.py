import asyncio
import json
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Header, HTTPException
from src.api.endpoints import perplexity
from src.config import settings
from src.services.factory import ServiceFactory
from src.schemas.perplexity import PerplexityRequest

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="CodeReview Agent", version="1.0.0")

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["chrome-extension://*", "http://localhost:*", "http://127.0.0.1:*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Validate configuration on startup
settings.validate_config()

# Utility endpoints
@app.get("/health")
def health_check():
    return {"status": "ok", "version": "1.0.0"}

@app.get("/models")
def list_models():
    return {"valid": ["sonar-pro", "llama-3.1-sonar-large-128k-online", "llama-3.1-sonar-small-128k-online"]}

app.include_router(perplexity.router)

@app.get("/")
async def root():
    return {"message": "CodeReview Agent API is running"}

@app.post("/analyze")
async def analyze_code(request: PerplexityRequest, api_key: str = Header(None)):
    if not api_key:
        raise HTTPException(status_code=401, detail="API Key required")
    client = ServiceFactory.get_client("perplexity", api_key=api_key)
    response = await client.analyze(request)
    return response

@app.get("/github/{owner}/{repo}/pr/{id}")
async def get_pr_analysis(owner: str, repo: str, id: int, api_key: str = Header(None)):
    if not api_key:
        raise HTTPException(status_code=401, detail="API Key required")
    # Simulation of scraping/fetching PR info and analyzing
    client = ServiceFactory.get_client("perplexity", api_key=api_key)
    request = PerplexityRequest(
        query=f"Analyze GitHub PR {owner}/{repo}#{id}",
        context=f"Requesting analysis for PR {id} on {owner}/{repo}"
    )
    response = await client.analyze(request)
    return response

from pydantic import BaseModel

class CIAnalyzeRequest(BaseModel):
    ci_log: str
    repo_path: str = "."

async def trigger_ide_agent(solution_md: str):
    # This simulates a bridge to an IDE agent (e.g., file watcher or local socket)
    # In a real scenario, this would send a message to a Claude Code or Continue instance
    ide_url = os.getenv("IDE_WS_URL", "ws://localhost:8080")
    print(f"Triggering IDE Auto-Fix via {ide_url}")
    # Integration logic here...

@app.post("/ci-analyze")
async def ci_analyze(request: CIAnalyzeRequest):
    from src.agents.multi_agent import MultiAgentAnalyzer
    analyzer = MultiAgentAnalyzer()
    result = await analyzer.analyze_ci_failure(
        request.ci_log, request.repo_path
    )
    # Trigger IDE
    await trigger_ide_agent(result["solution"])
    return {"status": "analysis_complete", "fix_plan": result["solution"], "files": result["files"],
            "run_id": result["run_id"]}

@app.post("/local-diff")
async def get_local_diff(path: str = ".", api_key: str = Header(None)):
    from src.services.git_service import GitService
    from src.config import find_repo_root
    repo_path = find_repo_root(path)
    git_service = GitService(str(repo_path))
    return {
        "status": git_service.get_status_summary(),
        "diff": git_service.get_diff(),
        "unpushed": git_service.get_unpushed_changes(),
        "resolved_path": str(repo_path)
    }

BROWSER_TOOL_TIMEOUT_SECONDS = 8.0

_background_tasks: set = set()


async def learn_in_background(kb, run_id: str) -> None:
    """Ingests a run with a verdict into the project wiki (one LLM call) and regenerates
    its graph."""
    from src.agents.llm import LLM
    from src.agents.universal_agent import TEXT_MODEL
    from src.kb.cache import ResponseCache
    try:
        pages = await kb.ingest(run_id, LLM(ResponseCache()), TEXT_MODEL)
        print(f"DEBUG: ingested run {run_id} into {len(pages)} wiki page(s): {pages}", flush=True)
    except Exception as e:
        print(f"WARNING: ingesting run {run_id} failed: {e}", flush=True)


def handle_feedback(message: dict) -> dict:
    """Records a 👍/👎 on a run and schedules the wiki update. Ingest costs an LLM call,
    so it runs after the reply, off the request path."""
    from src.kb.wiki import KnowledgeBase
    run_ref = message.get("run_ref") or {}
    kb = KnowledgeBase.for_project(run_ref.get("repo"), run_ref.get("page_url"))
    run_id = run_ref.get("run_id", "")
    if not kb.record_feedback(run_id, bool(message.get("worked")), message.get("note", "")):
        return {"type": "feedback_saved", "run_id": run_id, "duplicate": True}
    task = asyncio.create_task(learn_in_background(kb, run_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"type": "feedback_saved", "run_id": run_id, "mistakes_file": str(kb.mistakes_path)}


async def request_browser_tool(websocket: WebSocket, pending: list, tool: str, args: dict) -> str:
    """Asks the extension to run `tool` on the analyzed tab and waits for its tool_result.

    The main loop isn't reading the socket while an analysis runs, so this reads it here;
    pings are answered and any other message is queued in `pending` for the main loop.
    """
    request_id = os.urandom(4).hex()
    await websocket.send_json({"type": "tool_request", "id": request_id, "tool": tool, "args": args})

    async def wait_for_result():
        while True:
            message = json.loads(await websocket.receive_text())
            if message.get("type") == "tool_result" and message.get("id") == request_id:
                result = message.get("result", "")
                return result if isinstance(result, str) else json.dumps(result)
            if message.get("type") == "ping":
                await websocket.send_json({"type": "pong", "status": "active"})
            elif message.get("type") != "tool_result":
                pending.append(message)

    try:
        return await asyncio.wait_for(wait_for_result(), BROWSER_TOOL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return f"Error: the browser did not answer {tool} within {BROWSER_TOOL_TIMEOUT_SECONDS:.0f}s"


async def verify_fix(websocket: WebSocket, pending: list, message: dict) -> dict:
    """Re-checks a run's verification_checks on the (reloaded) page: element checks through
    inspect_element round trips, error checks against the errors captured after reload.
    No LLM call. The result is stored with the run as evidence; the verdict stays the user's."""
    from src.kb.wiki import KnowledgeBase
    from src.verify import describe, element_selectors, evaluate

    run_ref = message.get("run_ref") or {}
    kb = KnowledgeBase.for_project(run_ref.get("repo"), run_ref.get("page_url"))
    run = kb.load_run(run_ref.get("run_id", ""))
    checks = run.get("verification_checks") or []
    if not checks:
        return {"type": "verification_result", "run_id": run["run_id"], "passed": False, "results": [],
                "message": "This fix plan has no browser checks to run."}

    inspections = {}
    for selector in element_selectors(checks):
        answer = await request_browser_tool(websocket, pending, "inspect_element", {"selector": selector})
        try:
            inspections[selector] = json.loads(answer)
        except ValueError:
            inspections[selector] = answer  # "No elements match ..." / "Error: ..."
    result = evaluate(checks, inspections, message.get("console_errors", []), message.get("network_errors", []))
    kb.record_verification(run["run_id"], result)
    for item in result["results"]:
        item["label"] = describe(item["check"])
    return {"type": "verification_result", "run_id": run["run_id"], **result}


@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print(">>> BACKEND BOOTED - LISTENING FOR CONNECTIONS <<<", flush=True)
    print(f"DEBUG: WebSocket correlation ID: {os.urandom(4).hex()}", flush=True)
    print(f"DEBUG: WebSocket connection established from {websocket.client}", flush=True)
    pending: list = []
    try:
        while True:
            if pending:
                message = pending.pop(0)
            else:
                message = json.loads(await websocket.receive_text())
            msg_type = message.get("type")
            print(f"DEBUG: WebSocket Received -> {msg_type}", flush=True)
            
            if msg_type == "ping":
                await websocket.send_json({"type": "pong", "status": "active"})
                continue
            if msg_type == "tool_result":
                # A browser tool answer that arrived after its request timed out.
                continue
            if msg_type == "verify":
                try:
                    await websocket.send_json(await verify_fix(websocket, pending, message))
                except (ValueError, OSError) as e:
                    await websocket.send_json({"type": "error", "message": f"Verification failed: {e}"})
                continue
            if msg_type == "feedback":
                try:
                    await websocket.send_json(handle_feedback(message))
                except (ValueError, OSError) as e:
                    await websocket.send_json({"type": "error", "message": f"Feedback not saved: {e}"})
                continue

            # API Key fallback logic
            api_key = message.get("api_key") or os.getenv("PERPLEXITY_API_KEY", "").strip('"')
            
            if message.get("type") == "analyze_url":
                url = message.get("url")
                try:
                    client = ServiceFactory.get_client("perplexity", api_key=api_key)
                    request = PerplexityRequest(
                        query=f"Analyze this GitHub URL: {url}",
                        context="User is asking for a code review of this page."
                    )
                    response = await client.analyze(request)
                    await websocket.send_json({
                        "type": "analysis_result",
                        "answer": response.answer,
                        "metadata": response.metadata
                    })
                except Exception as e:
                    await websocket.send_json({"type": "error", "message": str(e)})
            
            elif message.get("type") == "analyze_logs":
                logs = message.get("logs")
                try:
                    client = ServiceFactory.get_client("perplexity", api_key=api_key)
                    request = PerplexityRequest(
                        query="Analyze these GitHub Actions failure logs and find the root cause.",
                        context=logs
                    )
                    response = await client.analyze(request)
                    await websocket.send_json({
                        "type": "analysis_result",
                        "answer": response.answer,
                        "metadata": response.metadata
                    })
                except Exception as e:
                    await websocket.send_json({"type": "error", "message": str(e)})

            elif message.get("type") == "analyze_local":
                from src.services.git_service import GitService
                from src.config import find_repo_root
                path_hint = message.get("path", ".")
                repo_path = find_repo_root(path_hint)
                git_service = GitService(str(repo_path))
                
                local_context = f"""
                Local Repository (Resolved: {repo_path}) Status:
                {git_service.get_status_summary()}
                
                Unstaged Diffs:
                {git_service.get_diff()}
                
                Unpushed Changes:
                {git_service.get_unpushed_changes()}
                """
                
                try:
                    client = ServiceFactory.get_client("perplexity", api_key=api_key)
                    request = PerplexityRequest(
                        query="Review these local changes and unpushed commits for bugs or improvements.",
                        context=local_context
                    )
                    response = await client.analyze(request)
                    await websocket.send_json({
                        "type": "analysis_result",
                        "answer": response.answer,
                        "metadata": response.metadata
                    })
                except Exception as e:
                    await websocket.send_json({"type": "error", "message": str(e)})
            
            elif message.get("type") == "universal_analyze":
                from src.agents.universal_agent import UniversalAgent
                query = message.get("query") or ""
                screenshot = message.get("screenshot")
                dom = message.get("dom") or {}
                repo = message.get("repo", ".")
                page_url = dom.get("url")

                try:
                    analyzer = UniversalAgent()
                    network_errors = message.get("network_errors", [])
                    console_errors = message.get("console_errors", [])

                    print(f"DEBUG: Starting Universal Analysis for query: {query}", flush=True)
                    print(f"DEBUG: Network Errors: {len(network_errors)}, Console Errors: {len(console_errors)}", flush=True)

                    async def browser_tool(name: str, args: dict) -> str:
                        return await request_browser_tool(websocket, pending, name, args)

                    # 1. Visual Analysis (may inspect the live page through the extension)
                    await websocket.send_json({"type": "status", "message": "Analyzing Visual Context..."})
                    ui_analysis = await analyzer.visual_agent(
                        screenshot, query, dom, network_errors, console_errors, browser_tool=browser_tool
                    )

                    # 2. Code Analysis + fix plan (reads the local repo through MCP when mapped)
                    await websocket.send_json({"type": "status", "message": "Reviewing Codebase..."})
                    code_analysis = await analyzer.code_agent(
                        repo, ui_analysis, dom.get("selectedElement") or {}, query=query, page_url=page_url,
                        browser_tool=browser_tool, network_errors=network_errors, console_errors=console_errors,
                    )

                    # 3. Render, remember, save and trigger IDE (no LLM calls)
                    fix_plan = await analyzer.integrator(ui_analysis, code_analysis)
                    run_id = analyzer.record_run(query, repo, page_url, code_analysis, network_errors, console_errors)
                    md_path = analyzer.save_universal_mds(fix_plan, query, repo, network_errors, console_errors, screenshot)
                    await trigger_ide_agent(fix_plan)
                    print(f"DEBUG: Universal Analysis done with {analyzer.llm.calls} LLM call(s)", flush=True)

                    await websocket.send_json({
                        "type": "analysis_result",
                        "answer": f"Universal Fix Plan Ready!\n\nUser Query: {query}\n\nFiles saved: {md_path}\n\nPlan:\n{fix_plan}",
                        "metadata": {"source": "groq_universal_agent", "llm_calls": analyzer.llm.calls},
                        "run_ref": {"run_id": run_id, "repo": repo, "page_url": page_url},
                    })
                except Exception as e:
                    await websocket.send_json({"type": "error", "message": f"Universal Analysis error: {str(e)}"})

            elif message.get("type") == "ci_analyze":
                print("DEBUG: Received ci_analyze", flush=True)
                from src.agents.multi_agent import MultiAgentAnalyzer
                ci_log = message.get("ci_log")
                repo = message.get("repo", ".")
                try:
                    analyzer = MultiAgentAnalyzer()
                    print(f"DEBUG: Starting analysis for {repo}", flush=True)
                    await websocket.send_json({"type": "status", "message": "Running Multi-Agent Swarm..."})
                    result = await analyzer.analyze_ci_failure(ci_log, repo)
                    # Trigger IDE
                    print("DEBUG: Triggering IDE agent", flush=True)
                    await trigger_ide_agent(result["solution"])
                    await websocket.send_json({
                        "type": "analysis_result",
                        "answer": f"Multi-Agent CI Solution Plan created!\n\nFiles saved: {list(result['files'].values())}\n\nPlan Summary:\n{result['solution']}",
                        "metadata": {"source": "groq_multi_agent", "llm_calls": analyzer.llm.calls},
                        "run_ref": {"run_id": result["run_id"], "repo": repo, "page_url": None},
                    })
                except Exception as e:
                    print(f"DEBUG: CI Analysis error: {str(e)}", flush=True)
                    await websocket.send_json({"type": "error", "message": f"CI Analysis error: {str(e)}"})

            elif message.get("type") == "ping":
                # Heartbeat to keep connection alive
                pass
            
            else:
                await websocket.send_json({"type": "echo", "message": message})
                
    except WebSocketDisconnect:
        print("Client disconnected")
    except Exception as e:
        print(f"WebSocket error: {e}")

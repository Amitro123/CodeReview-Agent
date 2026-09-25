import asyncio
import json
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from src.config import settings

app = FastAPI(title="CodeReview Agent", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["chrome-extension://*", "http://localhost:*", "http://127.0.0.1:*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

settings.validate_config()


@app.get("/health")
def health_check():
    return {"status": "ok", "version": app.version, "provider": settings.llm.provider}


BROWSER_TOOL_TIMEOUT_SECONDS = 8.0

_background_tasks: set = set()


async def learn_in_background(kb, run_id: str) -> None:
    """Ingests a run with a verdict into the project wiki (one LLM call) and regenerates
    its graph."""
    from src.agents.llm import LLM
    from src.kb.cache import ResponseCache
    try:
        pages = await kb.ingest(run_id, LLM(ResponseCache()), settings.llm.text_model)
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


async def handle_analyze(websocket: WebSocket, pending: list, message: dict) -> None:
    """The routed flow for any reported problem: classify it (Jev through OpenRouter, or one
    LLM call), tell the side panel where it's going, then run the chosen agents. When the
    classifier isn't sure, the panel asks the user and re-sends with `force_category`."""
    from src.router.pipeline import RoutedAnalysis, describe_route, route_message

    async def browser_tool(name: str, args: dict) -> str:
        return await request_browser_tool(websocket, pending, name, args)

    async def status(text: str) -> None:
        await websocket.send_json({"type": "status", "message": text})

    pipeline = RoutedAnalysis()
    if not message.get("force_category"):
        await status("Classifying the problem...")
    c, route = await pipeline.classify(message)
    print(f"DEBUG: route {route.categories or 'ask user'} via {c.method} ({c.category} {c.confidence:.2f})", flush=True)
    await websocket.send_json(route_message(c, route))
    if route.ask_user:
        return
    result = await pipeline.run(message, c, route, browser_tool=browser_tool, status=status)
    print(f"DEBUG: routed analysis done with {pipeline.llm_calls} LLM call(s) and "
          f"{pipeline.classifier_calls} Jev call(s)", flush=True)
    escalation = result.get("escalation")
    escalated = f"\n↑ Retried on {escalation['to']} ({escalation['reason']})" if escalation else ""
    await websocket.send_json({
        "type": "analysis_result",
        "answer": f"{describe_route(c, route)}{escalated}\n\nFiles saved: {result['files']}\n\n{result['plan']}",
        "metadata": {"source": "router", "llm_calls": pipeline.llm_calls,
                     "classifier_calls": pipeline.classifier_calls, "provider": settings.llm.provider,
                     "route": {"category": c.category, "confidence": c.confidence, "method": c.method,
                               "agents": route.categories, "sensitive": route.sensitive,
                               "escalation": escalation}},
        "run_ref": {"run_id": result["run_id"], "repo": result["repo"], "page_url": result["page_url"],
                    "category": c.category},
    })


@app.websocket("/ws/chat")
async def websocket_endpoint(websocket: WebSocket):
    """The side panel's channel. Messages: analyze (a reported problem), verify (re-check a
    fix), feedback (👍/👎), ping; tool_result answers a tool_request sent during an analysis."""
    await websocket.accept()
    print(f"DEBUG: WebSocket connection from {websocket.client}", flush=True)
    pending: list = []
    try:
        while True:
            message = pending.pop(0) if pending else json.loads(await websocket.receive_text())
            msg_type = message.get("type")
            print(f"DEBUG: WebSocket received -> {msg_type}", flush=True)

            if msg_type == "ping":
                await websocket.send_json({"type": "pong", "status": "active"})
            elif msg_type == "tool_result":
                pass  # a browser tool answer that arrived after its request timed out
            elif msg_type == "analyze":
                try:
                    await handle_analyze(websocket, pending, message)
                except Exception as e:
                    print(f"DEBUG: routed analysis error: {e}", flush=True)
                    await websocket.send_json({"type": "error", "message": f"Analysis error: {e}"})
            elif msg_type == "verify":
                try:
                    await websocket.send_json(await verify_fix(websocket, pending, message))
                except (ValueError, OSError) as e:
                    await websocket.send_json({"type": "error", "message": f"Verification failed: {e}"})
            elif msg_type == "feedback":
                try:
                    await websocket.send_json(handle_feedback(message))
                except (ValueError, OSError) as e:
                    await websocket.send_json({"type": "error", "message": f"Feedback not saved: {e}"})
            else:
                await websocket.send_json({"type": "error", "message": f"Unknown message type: {msg_type!r}"})
    except WebSocketDisconnect:
        print("Client disconnected", flush=True)

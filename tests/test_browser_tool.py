import asyncio
import json

import src.main as main


class FakeWebSocket:
    def __init__(self, incoming):
        self.incoming = list(incoming)
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)

    async def receive_text(self):
        if not self.incoming:
            await asyncio.sleep(3600)
        item = self.incoming.pop(0)
        if callable(item):
            item = item(self.sent)
        return json.dumps(item)


def test_browser_tool_round_trip_answers_pings_and_queues_other_messages():
    def matching_result(sent):
        return {"type": "tool_result", "id": sent[0]["id"], "result": [{"selector": "#btn"}]}

    ws = FakeWebSocket([
        {"type": "ping"},
        {"type": "universal_analyze", "query": "second question"},
        {"type": "tool_result", "id": "stale", "result": "old"},
        matching_result,
    ])
    pending = []
    result = asyncio.run(main.request_browser_tool(ws, pending, "inspect_element", {"selector": "#btn"}))

    assert ws.sent[0]["type"] == "tool_request" and ws.sent[0]["tool"] == "inspect_element"
    assert {"type": "pong", "status": "active"} in ws.sent
    assert json.loads(result) == [{"selector": "#btn"}]
    assert pending == [{"type": "universal_analyze", "query": "second question"}]


def test_browser_tool_times_out(monkeypatch):
    monkeypatch.setattr(main, "BROWSER_TOOL_TIMEOUT_SECONDS", 0.05)
    result = asyncio.run(main.request_browser_tool(FakeWebSocket([]), [], "inspect_element", {}))
    assert result.startswith("Error: the browser did not answer")

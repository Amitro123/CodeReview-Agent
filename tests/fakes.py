import json


class _Function:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, call_id, name, args):
        self.id = call_id
        self.type = "function"
        self.function = _Function(name, json.dumps(args))

    def model_dump(self):
        return {"id": self.id, "type": self.type,
                "function": {"name": self.function.name, "arguments": self.function.arguments}}


class _Message:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Completion:
    def __init__(self, message):
        self.choices = [type("Choice", (), {"message": message})()]


class ScriptedGroq:
    """Stands in for the Groq client: replays scripted replies and records every request.

    A reply is {"json": obj} for a final answer, {"text": str} for a non-JSON one, or
    {"tool_calls": [(name, args), ...]}."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kwargs):
        self.requests.append(kwargs)
        reply = self.replies.pop(0)
        if "tool_calls" in reply:
            calls = [_ToolCall(f"call_{i}", name, args) for i, (name, args) in enumerate(reply["tool_calls"])]
            return _Completion(_Message(tool_calls=calls))
        if "text" in reply:
            return _Completion(_Message(content=reply["text"]))
        return _Completion(_Message(content=json.dumps(reply["json"])))

    def prompt(self, index):
        return self.requests[index]["messages"][0]["content"]

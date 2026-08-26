"""Tool-loop tests for the chat brain, with the Anthropic client mocked.

The streaming agentic loop is where the subtle bugs live — dropped tool results,
mismatched tool_use_id, a loop that never terminates. These run offline and cost
nothing, so they can be run on every change.

Run:  python tests/test_chat_loop.py
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import chat, config, console

console.init()

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if detail:
        print(f"       {detail}")
    if not condition:
        FAILURES.append(label)


# --------------------------------------------------------------- fake SDK objects
class Block:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeMessage:
    def __init__(self, content, stop_reason):
        self.content = content
        self.stop_reason = stop_reason


class FakeStream:
    def __init__(self, text, message):
        self.text_stream = iter(text)
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self._message


class FakeMessages:
    """Replays a scripted list of (text_chunks, content_blocks, stop_reason)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        text, content, stop = self.script.pop(0)
        return FakeStream(text, FakeMessage(content, stop))


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)


def install(script) -> FakeClient:
    """Patch the module's Anthropic constructor and force the key check to pass."""
    client = FakeClient(script)
    chat.anthropic = types.SimpleNamespace(Anthropic=lambda **kw: client)
    chat.configured = lambda: True
    config.secret = lambda name, default="": "test-key" if "ANTHROPIC" in name else default
    return client


# --------------------------------------------------------------- 1. plain answer
client = install([(["Hello ", "there."], [Block(type="text", text="Hello there.")], "end_turn")])
messages = [{"role": "user", "content": "hi"}]
out = "".join(chat.stream_reply(messages))
check("Plain answer streams and stops", out == "Hello there.", f"got {out!r}")
check(
    "History ends with the assistant turn",
    messages[-1]["role"] == "assistant" and len(messages) == 2,
    f"{len(messages)} messages",
)

# --------------------------------------------------------------- 2. tool round-trip
tool_block = Block(
    type="tool_use", id="toolu_01", name="get_sale_calendar", input={}
)
client = install([
    (["Let me check. "], [Block(type="text", text="Let me check. "), tool_block], "tool_use"),
    (["The Autumn Sale is next."], [Block(type="text", text="The Autumn Sale is next.")], "end_turn"),
])
messages = [{"role": "user", "content": "when is the next sale?"}]
seen: list[str] = []
out = "".join(chat.stream_reply(messages, on_tool=lambda n, p: seen.append(n)))

check("Tool round-trip streams both turns", out == "Let me check. The Autumn Sale is next.", out)
check("on_tool callback fired", seen == ["get_sale_calendar"], str(seen))
check("Two API calls made", len(client.messages.calls) == 2, str(len(client.messages.calls)))

roles = [m["role"] for m in messages]
check("Message roles ordered correctly", roles == ["user", "assistant", "user", "assistant"], str(roles))

results = messages[2]["content"]
check(
    "tool_result echoes the tool_use id",
    results[0]["type"] == "tool_result" and results[0]["tool_use_id"] == "toolu_01",
    str(results[0].get("tool_use_id")),
)
check(
    "tool_result carries real calendar data",
    "Autumn" in results[0]["content"],
    results[0]["content"][:60],
)

# --------------------------------------------------------------- 3. parallel tools
blocks = [
    Block(type="tool_use", id="t1", name="get_sale_calendar", input={}),
    Block(type="tool_use", id="t2", name="query_database",
          input={"sql": "SELECT COUNT(*) AS n FROM games"}),
]
client = install([
    ([""], blocks, "tool_use"),
    (["Done."], [Block(type="text", text="Done.")], "end_turn"),
])
messages = [{"role": "user", "content": "how many games and when is the sale?"}]
"".join(chat.stream_reply(messages))
returned = messages[2]["content"]
check(
    "Both parallel tool results returned in ONE user message",
    len(returned) == 2 and {r["tool_use_id"] for r in returned} == {"t1", "t2"},
    f"{len(returned)} result(s)",
)

# --------------------------------------------------------------- 4. runaway guard
client = install([
    ([""], [Block(type="tool_use", id=f"t{i}", name="get_sale_calendar", input={})], "tool_use")
    for i in range(chat.MAX_TOOL_ROUNDS + 3)
])
messages = [{"role": "user", "content": "loop forever"}]
out = "".join(chat.stream_reply(messages))
check(
    "Runaway tool loop is capped",
    len(client.messages.calls) == chat.MAX_TOOL_ROUNDS and "Stopped after" in out,
    f"{len(client.messages.calls)} calls",
)

# --------------------------------------------------------------- 5. refusal
client = install([([""], [Block(type="text", text="")], "refusal")])
messages = [{"role": "user", "content": "something disallowed"}]
out = "".join(chat.stream_reply(messages))
check("Refusal handled without crashing", "can't help" in out, out.strip())

# --------------------------------------------------------------- 6. request shape
client = install([(["ok"], [Block(type="text", text="ok")], "end_turn")])
"".join(chat.stream_reply([{"role": "user", "content": "hi"}]))
sent = client.messages.calls[0]
check("Uses claude-opus-5", sent["model"] == "claude-opus-5", sent["model"])
check("Tools are declared", len(sent["tools"]) == 3, str(len(sent.get("tools", []))))
check(
    "Effort set via output_config",
    sent.get("output_config", {}).get("effort") == chat.EFFORT,
    str(sent.get("output_config")),
)
check("No deprecated budget_tokens", "budget_tokens" not in str(sent.get("thinking", "")), "")

# --------------------------------------------------------------- summary
print("=" * 70)
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S):")
    for failure in FAILURES:
        print(f"  - {failure}")
    sys.exit(1)
print("All chat-loop cases passed.")

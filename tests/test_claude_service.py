"""ClaudeService's manual tool-use loop, mocked at the SDK client boundary.

Covers: prompt assembly, SKIP -> None, tool_use round trips, the (content,
is_error) handler convention, tool exceptions, pause_turn continuation, the
iteration cap, and which tools get advertised.
"""
import base64
from types import SimpleNamespace

from family_line_bot.services.claude import ClaudeService, _MAX_TOOL_ITERATIONS


# --- scripted anthropic client -------------------------------------------

def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_use_block(name, tool_input, block_id="tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=tool_input, id=block_id)


def response(blocks, stop_reason="end_turn"):
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )


class FakeMessages:
    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._scripted:
            raise AssertionError("the tool loop asked for more turns than scripted")
        return self._scripted.pop(0)


class FakeAnthropic:
    def __init__(self, scripted):
        self.messages = FakeMessages(scripted)


def make_service(scripted, **kwargs):
    svc = ClaudeService(api_key="sk-ant-test", model="claude-test", system_prompt="persona", **kwargs)
    svc._client = FakeAnthropic(scripted)
    return svc


def ask(svc, **overrides):
    kwargs = dict(context="", query="Alice: hi", quoted="", quoted_image=None)
    kwargs.update(overrides)
    return svc.ask_text(**kwargs)


# --- basic replies --------------------------------------------------------

def test_plain_text_reply_is_returned_stripped():
    svc = make_service([response([text_block("  好啊  ")])])
    assert ask(svc) == "好啊"


def test_skip_sentinel_maps_to_none():
    svc = make_service([response([text_block("SKIP")])])
    assert ask(svc) is None


def test_empty_reply_maps_to_none():
    svc = make_service([response([text_block("   ")])])
    assert ask(svc) is None


def test_skip_inside_a_longer_reply_is_not_treated_as_the_sentinel():
    svc = make_service([response([text_block("我先 SKIP 這題")])])
    assert ask(svc) == "我先 SKIP 這題"


def test_multiple_text_blocks_are_concatenated():
    svc = make_service([response([text_block("一"), text_block("二")])])
    assert ask(svc) == "一二"


# --- prompt assembly ------------------------------------------------------

def test_prompt_starts_with_the_current_time_line():
    svc = make_service([response([text_block("ok")])])
    ask(svc)
    prompt = svc._client.messages.calls[0]["messages"][0]["content"]
    assert prompt.startswith("[現在時間：")
    assert "（台灣時間）" in prompt


def test_prompt_orders_time_context_quoted_then_query():
    svc = make_service([response([text_block("ok")])])
    ask(svc, context="CTX\n", quoted="QUOTED\n", query="Alice: hi")
    prompt = svc._client.messages.calls[0]["messages"][0]["content"]
    assert prompt.index("CTX") < prompt.index("QUOTED") < prompt.index("Alice: hi")


def test_the_timestamp_lives_in_the_user_turn_not_the_system_prompt():
    # A per-request timestamp in the cached system prefix would defeat caching.
    svc = make_service([response([text_block("ok")])])
    ask(svc)
    system = svc._client.messages.calls[0]["system"][0]["text"]
    assert "現在時間" not in system


def test_system_prompt_is_marked_for_ephemeral_caching():
    svc = make_service([response([text_block("ok")])])
    ask(svc)
    system = svc._client.messages.calls[0]["system"][0]
    assert system["cache_control"] == {"type": "ephemeral"}


def test_temperature_is_lowered_so_skip_judgments_are_stable():
    svc = make_service([response([text_block("ok")])])
    ask(svc)
    assert svc._client.messages.calls[0]["temperature"] == 0.3


def test_quoted_image_is_sent_as_a_base64_image_block():
    svc = make_service([response([text_block("ok")])])
    ask(svc, quoted_image=b"\x01\x02\x03")
    content = svc._client.messages.calls[0]["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[1]["source"]["data"] == base64.standard_b64encode(b"\x01\x02\x03").decode()
    assert content[1]["source"]["media_type"] == "image/jpeg"


def test_skip_instruction_only_appears_when_skipping_is_allowed():
    svc = make_service([response([text_block("ok")]), response([text_block("ok")])])
    ask(svc, may_skip=False)
    assert "SKIP" not in svc._client.messages.calls[0]["system"][0]["text"]
    ask(svc, may_skip=True)
    assert "SKIP" in svc._client.messages.calls[1]["system"][0]["text"]


def test_memory_is_injected_only_when_the_memory_tool_is_wired():
    svc = make_service([response([text_block("ok")]), response([text_block("ok")])])
    ask(svc)
    assert "長期記憶" not in svc._client.messages.calls[0]["system"][0]["text"]
    ask(svc, memory="- Alice 對花生過敏", tool_handlers={"memory": lambda **k: ("", False)})
    system = svc._client.messages.calls[1]["system"][0]["text"]
    assert "對花生過敏" in system
    # And it must tell the model not to burn a round trip on `view`.
    assert "不應該" in system


def test_empty_memory_renders_a_placeholder_not_a_blank():
    svc = make_service([response([text_block("ok")])])
    ask(svc, memory="", tool_handlers={"memory": lambda **k: ("", False)})
    assert "（目前是空的）" in svc._client.messages.calls[0]["system"][0]["text"]


# --- tool advertisement ---------------------------------------------------

def tool_names(svc, index=0):
    return [t["name"] for t in svc._client.messages.calls[index]["tools"]]


def test_web_search_is_always_advertised():
    svc = make_service([response([text_block("ok")])])
    ask(svc)
    assert tool_names(svc) == ["web_search"]


def test_memory_and_thsr_tools_follow_their_handlers():
    svc = make_service([response([text_block("ok")])])
    ask(svc, tool_handlers={"memory": lambda **k: "", "search_thsr": lambda **k: ""})
    assert set(tool_names(svc)) == {"web_search", "memory", "search_thsr"}


# --- tool loop ------------------------------------------------------------

def test_tool_use_result_is_fed_back_and_the_final_text_returned():
    svc = make_service([
        response([tool_use_block("search_thsr", {"origin": "台北", "destination": "左營",
                                                 "date": "2026-09-06"})], stop_reason="tool_use"),
        response([text_block("最近一班 08:00")]),
    ])
    calls = []

    def search(**kw):
        calls.append(kw)
        return "0801 08:00→09:36"

    assert ask(svc, tool_handlers={"search_thsr": search}) == "最近一班 08:00"
    assert calls == [{"origin": "台北", "destination": "左營", "date": "2026-09-06"}]

    second_turn = svc._client.messages.calls[1]["messages"]
    tool_result = second_turn[-1]["content"][0]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "tu_1"
    assert tool_result["content"] == "0801 08:00→09:36"
    assert "is_error" not in tool_result


def test_handler_error_tuple_sets_is_error_on_the_tool_result():
    svc = make_service([
        response([tool_use_block("memory", {"command": "view", "path": "/memories/x.md"})],
                 stop_reason="tool_use"),
        response([text_block("好")]),
    ])
    ask(svc, tool_handlers={"memory": lambda **kw: ("does not exist", True)})
    tool_result = svc._client.messages.calls[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert tool_result["content"] == "does not exist"


def test_a_raising_handler_becomes_an_error_result_not_a_crash():
    svc = make_service([
        response([tool_use_block("search_thsr", {"origin": "x"})], stop_reason="tool_use"),
        response([text_block("查詢失敗了")]),
    ])

    def boom(**kw):
        raise RuntimeError("network down")

    assert ask(svc, tool_handlers={"search_thsr": boom}) == "查詢失敗了"
    tool_result = svc._client.messages.calls[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert "network down" in tool_result["content"]


def test_an_unknown_tool_name_is_reported_back_to_the_model():
    svc = make_service([
        response([tool_use_block("mystery", {})], stop_reason="tool_use"),
        response([text_block("ok")]),
    ])
    ask(svc, tool_handlers={"memory": lambda **kw: ("", False)})
    tool_result = svc._client.messages.calls[1]["messages"][-1]["content"][0]
    assert "Unknown tool: mystery" in tool_result["content"]


def test_several_tool_calls_in_one_turn_all_get_results():
    svc = make_service([
        response(
            [tool_use_block("memory", {"command": "view"}, block_id="a"),
             tool_use_block("memory", {"command": "view"}, block_id="b")],
            stop_reason="tool_use",
        ),
        response([text_block("ok")]),
    ])
    ask(svc, tool_handlers={"memory": lambda **kw: ("fine", False)})
    results = svc._client.messages.calls[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]


def test_pause_turn_resends_the_conversation_without_calling_a_handler():
    """Server-side web_search hit its iteration limit; resume by resending."""
    svc = make_service([
        response([text_block("searching...")], stop_reason="pause_turn"),
        response([text_block("找到了")]),
    ])
    assert ask(svc) == "找到了"
    resumed = svc._client.messages.calls[1]["messages"]
    assert len(resumed) == 2 and resumed[1]["role"] == "assistant"


def test_the_tool_loop_is_bounded():
    # A model stuck in a tool_use loop must not spin forever.
    svc = make_service([
        response([tool_use_block("memory", {"command": "view"})], stop_reason="tool_use")
        for _ in range(_MAX_TOOL_ITERATIONS)
    ])
    assert ask(svc, tool_handlers={"memory": lambda **kw: ("ok", False)}) is None
    assert len(svc._client.messages.calls) == _MAX_TOOL_ITERATIONS


# --- image description ----------------------------------------------------

def test_ask_image_sends_the_image_and_returns_the_description():
    svc = make_service([response([text_block(" 好可愛的貓 ")])])
    assert svc.ask_image("CTX\n", b"jpg") == "好可愛的貓"
    content = svc._client.messages.calls[0]["messages"][0]["content"]
    assert content[0]["text"].startswith("CTX\n")
    assert content[1]["source"]["data"] == base64.standard_b64encode(b"jpg").decode()

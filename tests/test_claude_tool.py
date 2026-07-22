"""结构化 AI 调用的代理兼容契约测试。"""
from types import SimpleNamespace

from utils import claude


def test_tool_call_falls_back_when_provider_rejects_thinking(monkeypatch):
    """MiMo 等代理拒绝 thinking 参数时，应去掉该参数重试同一工具调用。"""
    class FakeBadRequest(Exception):
        pass

    class FakeToolUseBlock:
        def __init__(self, input_data):
            self.input = input_data

    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise FakeBadRequest("thinking unsupported")
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            stop_reason="tool_use",
            content=[FakeToolUseBlock({"results": [{"id": 1}]})],
        )

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(claude.anthropic, "BadRequestError", FakeBadRequest)
    monkeypatch.setattr(claude, "ToolUseBlock", FakeToolUseBlock)
    monkeypatch.setattr(claude, "get_client", lambda: client)
    monkeypatch.setattr(claude, "_get_model", lambda: "mimo-test")

    result = claude.claude_call_tool(
        "Rate the item",
        tool_name="rate",
        tool_description="Rate one item",
        input_schema={"type": "object"},
    )

    assert result == {"results": [{"id": 1}]}
    assert calls[0]["thinking"] == {"type": "disabled"}
    assert "thinking" not in calls[1]
    assert calls[0]["tool_choice"] == calls[1]["tool_choice"]

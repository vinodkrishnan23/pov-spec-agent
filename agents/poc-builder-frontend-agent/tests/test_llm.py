import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import llm


def test_openai_gateway_uses_chat_completions_without_stream_usage(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setitem(
        sys.modules, "langchain_openai", types.SimpleNamespace(ChatOpenAI=FakeChatOpenAI)
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example/openai/v1/")

    llm.build_llm()

    assert captured["base_url"] == "https://gateway.example/openai/v1"
    assert captured["use_responses_api"] is False
    assert captured["stream_usage"] is False
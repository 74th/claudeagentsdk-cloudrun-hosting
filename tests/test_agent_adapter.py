import asyncio
import logging
import sys
from pathlib import Path
from types import ModuleType

import pytest

from cas_hosting_adapter.agent_adapter import ClaudeAgentAdapter


class FakeOptions:
    def __init__(self, **values: object) -> None:
        self.values = values


class FakeResultMessage:
    default_total_cost_usd: object = None
    default_duration_ms: object = None
    default_is_error = False

    def __init__(self, session_id: str, result: str) -> None:
        self.session_id = session_id
        self.result = result
        self.is_error = self.default_is_error
        self.uuid = "result-1"
        self.total_cost_usd = self.default_total_cost_usd
        self.duration_ms = self.default_duration_ms


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> list[FakeOptions]:
    captured: list[FakeOptions] = []
    module = ModuleType("claude_agent_sdk")
    module.ClaudeAgentOptions = FakeOptions  # type: ignore[attr-defined]
    module.ResultMessage = FakeResultMessage  # type: ignore[attr-defined]

    async def query(*, prompt: str, options: FakeOptions):
        assert prompt == "hello"
        captured.append(options)
        yield FakeResultMessage("sdk-session", "done")

    module.query = query  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", module)
    return captured


@pytest.mark.asyncio
async def test_agent_uses_job_scoped_transcript_directory_for_new_and_resumed_runs(
    tmp_path: Path, fake_sdk: list[FakeOptions]
) -> None:
    adapter = ClaudeAgentAdapter(model="test-model")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transcript = tmp_path / "run-1" / "claude-session"

    assert (
        await adapter.run(prompt="hello", workspace=workspace, transcript_dir=transcript) == "done"
    )
    assert (
        await adapter.run(
            prompt="hello", workspace=workspace, transcript_dir=transcript, resume="sdk-session"
        )
        == "done"
    )

    assert transcript.is_dir()
    initial, resumed = fake_sdk
    assert initial.values["env"]["HOME"] == str(transcript.resolve())
    assert "resume" not in initial.values
    assert resumed.values["resume"] == "sdk-session"
    assert "session_store" in resumed.values


@pytest.mark.asyncio
async def test_restored_transcript_session_store_loads_jsonl(tmp_path: Path) -> None:
    from cas_hosting_adapter.agent_adapter import RestoredTranscriptSessionStore

    transcript = tmp_path / ".claude" / "projects" / "old-workspace"
    transcript.mkdir(parents=True)
    (transcript / "sdk-session.jsonl").write_text(
        '{"type":"user","uuid":"one"}\n{"type":"assistant","uuid":"two"}\n',
        encoding="utf-8",
    )

    entries = await RestoredTranscriptSessionStore(tmp_path).load({"session_id": "sdk-session"})

    assert entries == [
        {"type": "user", "uuid": "one"},
        {"type": "assistant", "uuid": "two"},
    ]


@pytest.mark.asyncio
async def test_agent_logs_sdk_start_message_and_result(
    tmp_path: Path, fake_sdk: list[FakeOptions], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="cas_hosting_adapter.agent_adapter")
    adapter = ClaudeAgentAdapter(model="test-model")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert (
        await adapter.run(
            prompt="hello", workspace=workspace, transcript_dir=tmp_path / "transcript"
        )
        == "done"
    )
    assert "claude_sdk.query.start model=test-model resume=False" in caplog.text
    assert "claude_sdk.query.result position=0 is_error=False" in caplog.text


@pytest.mark.asyncio
async def test_result_message_metadata_is_persisted_on_final_event(
    tmp_path: Path, fake_sdk: list[FakeOptions], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FakeResultMessage, "default_total_cost_usd", 0.012345)
    monkeypatch.setattr(FakeResultMessage, "default_duration_ms", 1234)
    adapter = ClaudeAgentAdapter(model="test-model")

    events = [
        event
        async for event in adapter.events(
            prompt="hello", workspace=tmp_path, transcript_dir=tmp_path / "transcript"
        )
    ]

    assert events[-1]["event_type"] == "final"
    assert events[-1]["payload"] == {
        "output": "done",
        "estimated_cost_usd": 0.012345,
        "duration_ms": 1234,
    }


@pytest.mark.asyncio
async def test_result_message_metadata_omits_invalid_values(
    tmp_path: Path, fake_sdk: list[FakeOptions], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FakeResultMessage, "default_total_cost_usd", float("nan"))
    monkeypatch.setattr(FakeResultMessage, "default_duration_ms", -1)
    adapter = ClaudeAgentAdapter(model="test-model")

    events = [
        event
        async for event in adapter.events(
            prompt="hello", workspace=tmp_path, transcript_dir=tmp_path / "transcript"
        )
    ]

    assert events[-1]["payload"] == {"output": "done"}


@pytest.mark.asyncio
async def test_error_result_keeps_available_metadata(
    tmp_path: Path, fake_sdk: list[FakeOptions], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FakeResultMessage, "default_is_error", True)
    monkeypatch.setattr(FakeResultMessage, "default_total_cost_usd", 0.045)
    monkeypatch.setattr(FakeResultMessage, "default_duration_ms", None)
    adapter = ClaudeAgentAdapter(model="test-model")

    events = [
        event
        async for event in adapter.events(
            prompt="hello", workspace=tmp_path, transcript_dir=tmp_path / "transcript"
        )
    ]

    assert events[-1]["event_type"] == "error"
    assert events[-1]["payload"] == {"output": "done", "estimated_cost_usd": 0.045}


@pytest.mark.asyncio
async def test_sdk_total_cost_is_used_as_is_for_search_inclusive_estimate(
    tmp_path: Path, fake_sdk: list[FakeOptions], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The SDK total already represents the whole query, including Web Search.
    monkeypatch.setattr(FakeResultMessage, "default_total_cost_usd", 0.123456)
    monkeypatch.setattr(FakeResultMessage, "default_duration_ms", 2000)
    adapter = ClaudeAgentAdapter(model="test-model")

    events = [
        event
        async for event in adapter.events(
            prompt="hello", workspace=tmp_path, transcript_dir=tmp_path / "transcript"
        )
    ]

    assert events[-1]["payload"]["estimated_cost_usd"] == 0.123456


def test_sdk_messages_are_normalised_with_stable_event_ids() -> None:
    class TextBlock:
        text = "answer"

    class ToolUseBlock:
        id = "tool-1"
        name = "Read"
        input = {"path": "README.md"}

    class AssistantMessage:
        uuid = "message-1"
        content = [TextBlock(), ToolUseBlock()]
        error = None

    events = ClaudeAgentAdapter(model="test-model")._normalise_message(
        AssistantMessage(), position=3
    )

    assert [(event["event_type"], event["event_id"]) for event in events] == [
        ("agent", "sdk:message-1:0"),
        ("tool_started", "sdk:message-1:1"),
    ]


def test_user_tool_result_messages_are_not_stored_as_user_events() -> None:
    class ToolResultBlock:
        tool_use_id = "tool-1"
        content = [{"type": "text", "text": "result"}]
        is_error = False

    class UserMessage:
        uuid = "message-2"
        content = [ToolResultBlock()]

    events = ClaudeAgentAdapter(model="test-model")._normalise_message(UserMessage(), position=4)

    assert [(event["event_type"], event["payload"]) for event in events] == [
        (
            "tool_completed",
            {
                "tool_id": "tool-1",
                "content": [{"type": "text", "text": "result"}],
                "is_error": False,
            },
        )
    ]


def test_question_broker_enables_sdk_streaming_permission_mode(tmp_path: Path) -> None:
    adapter = ClaudeAgentAdapter(model="test-model")
    options = adapter._options(
        workspace=tmp_path,
        transcript_dir=tmp_path / "transcript",
        resume=None,
        can_use_tool=lambda *_args: None,
    )
    assert options["permission_mode"] == "default"
    assert options["can_use_tool"] is not None


@pytest.mark.asyncio
async def test_streaming_prompt_uses_claude_sdk_user_envelope() -> None:
    messages = [message async for message in ClaudeAgentAdapter._prompt_stream("hello")]
    assert messages == [
        {
            "type": "user",
            "message": {"role": "user", "content": "hello"},
            "parent_tool_use_id": None,
            "session_id": "default",
        }
    ]


@pytest.mark.asyncio
async def test_streaming_prompt_stays_open_until_run_finishes() -> None:
    done = asyncio.Event()
    stream = ClaudeAgentAdapter._prompt_stream("hello", done)

    first = await anext(stream)
    assert first["message"]["content"] == "hello"

    waiting = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    assert not waiting.done()

    done.set()
    with pytest.raises(StopAsyncIteration):
        await waiting

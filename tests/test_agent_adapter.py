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
    def __init__(self, session_id: str, result: str) -> None:
        self.session_id = session_id
        self.result = result
        self.is_error = False
        self.uuid = "result-1"


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

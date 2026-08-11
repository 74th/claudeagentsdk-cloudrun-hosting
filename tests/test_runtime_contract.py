from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from cas_hosting_adapter import AgentUsageRecord, ClaudeAgentConfig, RuntimePolicy
from cas_hosting_adapter.agent_adapter import ClaudeAgentAdapter
from cas_hosting_adapter.errors import AgentError


def test_agent_config_is_immutable_and_validates_tools() -> None:
    config = ClaudeAgentConfig(system_prompt="system", model="model", allowed_tools=["Read"])
    assert config.allowed_tools == ("Read",)
    with pytest.raises((AttributeError, TypeError)):
        config.model = "other"  # type: ignore[misc]
    with pytest.raises(ValueError):
        ClaudeAgentConfig(model=" ")
    with pytest.raises(ValueError):
        ClaudeAgentConfig(allowed_tools=[" "])


def test_runtime_policy_has_shared_defaults_and_validates_limits() -> None:
    first = RuntimePolicy()
    resumed = RuntimePolicy()
    assert first == resumed
    with pytest.raises(ValueError):
        RuntimePolicy(snapshot_max_bytes=0)
    with pytest.raises(ValueError):
        RuntimePolicy(question_timeout=0)


def test_agent_usage_record_keeps_typed_values_and_missing_sdk_usage() -> None:
    run_id = uuid4()
    recorded_at = datetime(2026, 8, 11, 12, 34, 56, tzinfo=UTC)
    record = AgentUsageRecord(
        user_name="user@example.com",
        run_id=run_id,
        session_name="Repository task",
        estimated_cost_usd=None,
        recorded_at=recorded_at,
        duration_ms=None,
    )

    assert record.user_name == "user@example.com"
    assert record.run_id is run_id
    assert record.session_name == "Repository task"
    assert record.estimated_cost_usd is None
    assert record.recorded_at == recorded_at
    assert record.duration_ms is None
    with pytest.raises((AttributeError, TypeError)):
        record.session_name = "other"  # type: ignore[misc]


def test_agent_options_apply_config_without_allowing_framework_override(tmp_path: Path) -> None:
    adapter = ClaudeAgentAdapter(
        agent_config=ClaudeAgentConfig(
            system_prompt="system", model="model", allowed_tools=("Read",)
        )
    )
    options = adapter._options(
        workspace=tmp_path / "workspace",
        transcript_dir=tmp_path / "transcript",
        resume="session-1",
    )
    assert options["system_prompt"] == "system"
    assert options["allowed_tools"] == ["Read"]
    assert options["cwd"] == str((tmp_path / "workspace").resolve())
    assert options["env"]["HOME"] == str((tmp_path / "transcript").resolve())
    assert options["resume"] == "session-1"
    assert "session_store" in options


def test_resume_transcript_is_limited_to_session_and_detects_collision(tmp_path: Path) -> None:
    adapter = ClaudeAgentAdapter(model="model")
    projects = tmp_path / "transcript" / ".claude" / "projects"
    old = projects / "old-workspace"
    old.mkdir(parents=True)
    (old / "session.jsonl").write_text("one", encoding="utf-8")
    workspace = tmp_path / "new-workspace"
    workspace.mkdir()
    adapter._prepare_transcript_resume(tmp_path / "transcript", workspace, "session")
    destination = projects / adapter._workspace_key(workspace) / "session.jsonl"
    assert destination.read_text(encoding="utf-8") == "one"

    other = projects / "another-workspace"
    other.mkdir()
    (other / "session.jsonl").write_text("different", encoding="utf-8")
    with pytest.raises(AgentError):
        adapter._prepare_transcript_resume(tmp_path / "transcript", workspace, "session")

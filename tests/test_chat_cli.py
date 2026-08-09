from __future__ import annotations

import json
from uuid import uuid4

from cas_hosting_adapter.models import Run, RunState
from example.chat.cli import run_cli
from example.chat.events import ChatEventKind, CommonChatEvent
from example.chat.service import ChatStartResult


def test_cli_flushes_start_and_each_event_as_json_lines(monkeypatch, capsys, tmp_path) -> None:
    run_id = uuid4()
    run = Run(
        id=run_id,
        user_id="user",
        session_id="session",
        workspace_id="workspace",
        idempotency_key="key",
        state=RunState.COMPLETED,
    )

    class FakeService:
        def __init__(self, _client, user_id: str) -> None:
            assert user_id == "user"

        def start(self, prompt: str, **kwargs) -> ChatStartResult:
            assert prompt == "hello"
            assert kwargs == {"session_id": None, "idempotency_key": "key"}
            return ChatStartResult("session", run_id, run)

        def stream(self, _run):
            yield CommonChatEvent(
                id="agent-1",
                run_id=run_id,
                sequence=1,
                kind=ChatEventKind.AGENT,
                raw_type="agent",
                payload={"content": "hello"},
                content="hello",
            )

        def get_run(self, session_id, current_run_id):
            assert (session_id, current_run_id) == ("session", run_id)
            return run

    monkeypatch.setattr("example.chat.cli.ChatService", FakeService)
    monkeypatch.setattr(
        "example.chat.cli.create_control_client_from_release_config", lambda _path: object()
    )

    code = run_cli(
        [
            "--release-config",
            str(tmp_path / "release.yaml"),
            "--user-id",
            "user",
            "--prompt",
            "hello",
            "--idempotency-key",
            "key",
        ]
    )

    output = capsys.readouterr()
    assert code == 0
    records = [json.loads(line) for line in output.out.splitlines()]
    assert [record["type"] for record in records] == ["start", "event"]
    assert records[1]["event"]["content"] == "hello"
    assert output.err == ""


def test_cli_returns_nonzero_and_hides_exception_details(monkeypatch, capsys, tmp_path) -> None:
    class FailingService:
        def __init__(self, _client, _user_id: str) -> None:
            pass

        def start(self, _prompt: str, **_kwargs):
            raise RuntimeError("secret-token-should-not-be-printed")

    monkeypatch.setattr("example.chat.cli.ChatService", FailingService)
    monkeypatch.setattr(
        "example.chat.cli.create_control_client_from_release_config", lambda _path: object()
    )

    code = run_cli(
        [
            "--release-config",
            str(tmp_path / "release.yaml"),
            "--user-id",
            "user",
            "--prompt",
            "hello",
        ]
    )

    output = capsys.readouterr()
    assert code == 2
    assert "secret-token" not in output.err
    assert output.out == ""

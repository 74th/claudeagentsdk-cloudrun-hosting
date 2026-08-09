from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from cas_hosting_adapter.models import Run
from example.chat.service import ChatStartResult
from example.slackbot_frontend.handler import SlackMessageHandler
from example.slackbot_frontend.store import (
    InMemorySlackThreadSessionStore,
    SlackThreadKey,
    application_user_id,
)


class DirectExecutor:
    def submit(self, function, *args):
        function(*args)


class FakeService:
    def __init__(self, user_id: str, calls: list[tuple[str, str | None, str]]) -> None:
        self.user_id = user_id
        self.calls = calls

    def start(self, prompt: str, *, session_id=None, idempotency_key=None):
        self.calls.append((prompt, session_id, idempotency_key))
        run = Run(
            user_id=self.user_id,
            session_id=session_id or f"session-{len(self.calls)}",
            workspace_id="workspace",
            idempotency_key=idempotency_key or "key",
        )
        return ChatStartResult(run.session_id, run.id, run)

    def stream(self, _run):
        from example.chat.events import ChatEventKind, CommonChatEvent

        yield CommonChatEvent(
            id="agent",
            run_id=uuid4(),
            sequence=1,
            kind=ChatEventKind.AGENT,
            raw_type="agent",
            payload={"content": "応答"},
            content="応答",
        )
        yield CommonChatEvent(
            id="final",
            run_id=uuid4(),
            sequence=2,
            kind=ChatEventKind.FINAL,
            raw_type="final",
            payload={"output": "応答"},
            content="応答",
        )


class FakeSlackClient:
    def __init__(self) -> None:
        self.posts: list[dict[str, str]] = []
        self.updates: list[dict[str, str]] = []

    def chat_postMessage(self, **kwargs):
        self.posts.append(kwargs)
        return {"channel": kwargs["channel"], "ts": "response-ts"}

    def chat_update(self, **kwargs):
        self.updates.append(kwargs)
        return kwargs


def test_slack_handler_acknowledges_immediately_and_continues_thread() -> None:
    store = InMemorySlackThreadSessionStore()
    calls: list[tuple[str, str | None, str]] = []
    client = FakeSlackClient()
    handler = SlackMessageHandler(
        lambda user_id: FakeService(user_id, calls),
        store,
        bot_user_id="B-bot",
        update_interval=0,
        executor=DirectExecutor(),
    )
    ack_calls: list[str] = []
    event = {
        "user": "U-user",
        "text": "hello",
        "channel": "C-channel",
        "ts": "1.0",
        "event_id": "Ev-1",
    }

    handler.handle(event, lambda: ack_calls.append("ack"), client, team_id="T-team")
    handler.handle(
        {**event, "text": "follow", "event_id": "Ev-2", "thread_ts": "1.0"},
        lambda: ack_calls.append("ack"),
        client,
        team_id="T-team",
    )

    assert ack_calls == ["ack", "ack"]
    assert calls[0][1] is None
    assert calls[1][1] == "session-1"
    assert len(client.posts) == 2
    assert all(post["thread_ts"] == "1.0" for post in client.posts)
    assert client.updates
    assert "最終結果:\n応答" in client.updates[-1]["text"]
    key = SlackThreadKey("T-team", "C-channel", "1.0")
    binding = store.get(key)
    assert binding is not None
    assert binding.application_user_id == application_user_id("T-team", "U-user")


def test_slack_handler_ignores_own_messages_but_acknowledges_them() -> None:
    handler = SlackMessageHandler(
        lambda _user_id: None,
        InMemorySlackThreadSessionStore(),
        bot_user_id="B-bot",
        executor=DirectExecutor(),
    )
    acknowledged: list[bool] = []
    client = FakeSlackClient()

    handler.handle(
        {"user": "B-bot", "text": "bot", "channel": "C", "ts": "1"},
        lambda: acknowledged.append(True),
        client,
        team_id="T",
    )

    assert acknowledged == [True]
    assert client.posts == []


def test_slack_rate_limit_uses_retry_after() -> None:
    waits: list[float] = []
    handler = SlackMessageHandler(
        lambda _user_id: None,
        InMemorySlackThreadSessionStore(),
        bot_user_id="B",
        sleep=waits.append,
        executor=DirectExecutor(),
    )
    # Slack SDK exceptions expose response; use the same public shape in the double.
    response = SimpleNamespace(status_code=429, data={}, headers={"Retry-After": "2"})
    exception = RuntimeError("rate limited")
    exception.response = response  # type: ignore[attr-defined]
    calls = [exception]

    def method(**_kwargs):
        if calls:
            raise calls.pop()
        return "ok"

    assert handler._call_with_retry(method) == "ok"
    assert waits == [2.0]


def test_slack_message_keeps_result_and_respects_length_limit() -> None:
    message = SlackMessageHandler._compose_message(
        ["作業内容: " + "x" * 5000], "最終回答です", "completed"
    )

    assert len(message) <= 3900
    assert "最終結果:\n最終回答です" in message

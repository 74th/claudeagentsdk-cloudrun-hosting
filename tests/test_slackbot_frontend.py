from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from cas_hosting_adapter.models import (
    ChatEvent,
    QuestionOption,
    QuestionRequest,
    QuestionState,
    Run,
    RunPage,
    RunState,
    Session,
)
from example.chat.events import ChatEventKind, CommonChatEvent
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
            payload={
                "output": "応答",
                "estimated_cost_usd": 0.012345,
                "duration_ms": 1234,
            },
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


class PendingQuestionService(FakeService):
    def __init__(self, user_id: str, question: QuestionRequest, *, timed_out: bool) -> None:
        super().__init__(user_id, [])
        self.question = question
        self.session = Session(
            id=question.id + "-session",
            user_id=user_id,
            workspace_id="workspace",
            active_run_id=None if timed_out else question.run_id,
            latest_run_state=RunState.TIMED_OUT.value if timed_out else RunState.RUNNING.value,
        )
        self.continuations: list[tuple[str, list[str]]] = []
        self.continuation_run = Run(
            user_id=user_id,
            session_id=self.session.id,
            workspace_id="workspace",
            idempotency_key="continuation",
        )

    def get_session(self, _session_id: str) -> Session:
        return self.session

    def pending_questions(self, _session_id: str, run_id, list_unused=None):
        if run_id == self.question.run_id and self.question.state is QuestionState.PENDING:
            return [self.question]
        return []

    def list_runs(self, _session_id: str, *, cursor=None, limit=100):
        run = Run(
            id=self.question.run_id,
            user_id=self.user_id,
            session_id=self.session.id,
            workspace_id="workspace",
            idempotency_key="original",
            state=RunState.TIMED_OUT,
        )
        return RunPage(runs=[run], next_cursor=None)

    def continue_after_question(self, _session_id, question, answers, *, idempotency_key):
        self.continuations.append((question.id, list(answers)))
        return ChatStartResult(self.session.id, self.continuation_run.id, self.continuation_run)

    def stream(self, _run):
        yield CommonChatEvent(
            id="continuation-final",
            run_id=uuid4(),
            sequence=1,
            kind=ChatEventKind.FINAL,
            raw_type="final",
            payload={"output": "継続しました"},
            content="継続しました",
        )


def make_question(run_id=None) -> QuestionRequest:
    return QuestionRequest(
        id="question-1",
        run_id=run_id or uuid4(),
        ordinal=1,
        question="どれにしますか？",
        header="確認",
        options=[
            QuestionOption(label="A", description="A を選ぶ"),
            QuestionOption(label="B", description="B を選ぶ"),
            QuestionOption(label="C", description="C を選ぶ"),
        ],
        idempotency_key="question-key",
    )


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
    assert len(client.posts) == 4
    assert all(post["thread_ts"] == "1.0" for post in client.posts)
    assert client.updates
    assert "実行終了" not in client.updates[-1]["text"]
    assert any("最終結果:\n応答" in post["text"] for post in client.posts)
    assert any("推定価格 (USD): $0.012345" in post["text"] for post in client.posts)
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


def test_slack_history_fallback_appends_metadata_to_final_result() -> None:
    class HistoryFallbackService(FakeService):
        def __init__(self, user_id: str) -> None:
            super().__init__(user_id, [])
            self.started_run: Run | None = None

        def start(self, prompt: str, **kwargs):
            result = super().start(prompt, **kwargs)
            self.started_run = result.run
            return result

        def stream(self, run):
            yield CommonChatEvent(
                id="terminal",
                run_id=run.id,
                sequence=2,
                kind=ChatEventKind.TERMINAL,
                raw_type="terminal",
                payload={"state": "completed"},
                content=None,
            )

        def events(self, run_id):
            return [
                ChatEvent(
                    id="saved-final",
                    run_id=run_id,
                    sequence=1,
                    type="final",
                    payload={
                        "output": "履歴回答",
                        "estimated_cost_usd": 0.000001,
                        "duration_ms": 10,
                    },
                )
            ]

    service = HistoryFallbackService("app-user")
    client = FakeSlackClient()
    handler = SlackMessageHandler(
        lambda _user_id: service,
        InMemorySlackThreadSessionStore(),
        bot_user_id="B-bot",
        update_interval=0,
        executor=DirectExecutor(),
    )
    key = SlackThreadKey("T-team", "C-channel", "1.0")
    response = client.chat_postMessage(
        channel=key.channel_id, thread_ts=key.thread_ts, text="working"
    )

    handler._consume(
        service,
        Run(user_id="app-user", session_id="s", workspace_id="w", idempotency_key="k"),
        client,
        key,
        response,
    )

    assert any("推定価格 (USD): $0.000001" in post["text"] for post in client.posts)
    assert any("処理時間 (SDK): 0.01秒" in post["text"] for post in client.posts)


def test_slack_handler_posts_durable_pending_question() -> None:
    question = make_question()
    service = PendingQuestionService("app-user", question, timed_out=False)
    client = FakeSlackClient()
    handler = SlackMessageHandler(
        lambda _user_id: service,
        InMemorySlackThreadSessionStore(),
        bot_user_id="B-bot",
        update_interval=0,
        executor=DirectExecutor(),
    )
    key = SlackThreadKey("T-team", "C-channel", "1.0")
    response = client.chat_postMessage(
        channel=key.channel_id, thread_ts=key.thread_ts, text="working"
    )
    original_run = Run(
        id=question.run_id,
        user_id=service.user_id,
        session_id=service.session.id,
        workspace_id="workspace",
        idempotency_key="original",
    )

    handler._consume(service, original_run, client, key, response)

    assert any("どれにしますか？" in post["text"] for post in client.posts)


def test_slack_handler_continues_after_question_timeout() -> None:
    question = make_question()
    service = PendingQuestionService("app-user", question, timed_out=True)
    store = InMemorySlackThreadSessionStore()
    key = SlackThreadKey("T-team", "C-channel", "1.0")
    store.create_if_absent(
        key,
        application_user_id=application_user_id("T-team", "U-user"),
        session_id=service.session.id,
    )
    client = FakeSlackClient()
    handler = SlackMessageHandler(
        lambda _user_id: service,
        store,
        bot_user_id="B-bot",
        update_interval=0,
        executor=DirectExecutor(),
    )

    handler.handle(
        {
            "user": "U-user",
            "text": "2",
            "channel": key.channel_id,
            "thread_ts": key.thread_ts,
            "ts": "2.0",
            "event_id": "Ev-answer",
        },
        lambda: None,
        client,
        team_id=key.team_id,
    )

    assert service.continuations == [(question.id, ["B"])]
    assert any("回答を受け付けました" in post["text"] for post in client.posts)

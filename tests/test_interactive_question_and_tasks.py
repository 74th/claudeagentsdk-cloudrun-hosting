from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from cas_hosting_adapter.agent_adapter import AskUserQuestionBroker
from cas_hosting_adapter.control_client import ControlClient
from cas_hosting_adapter.errors import (
    QuestionClosedError,
    QuestionConflictError,
    QuestionNotFoundError,
    QuestionOwnershipError,
)
from cas_hosting_adapter.in_memory_chat_store import InMemoryChatStore
from cas_hosting_adapter.models import (
    ChatEvent,
    QuestionRequest,
    QuestionState,
    Run,
    RunState,
    Session,
)
from cas_hosting_adapter.protocols import InMemoryExecutionBackend
from example.chat.events import interaction_state, normalize_events
from example.chat.service import ChatService
from example.slackbot_frontend.handler import SlackMessageHandler, parse_question_answer
from example.slackbot_frontend.store import (
    InMemorySlackThreadSessionStore,
    SlackThreadKey,
    application_user_id,
)


def _run(store: InMemoryChatStore) -> Run:
    session = store.put_session(Session(id="session", user_id="user", workspace_id="workspace"))
    run = Run(
        user_id="user",
        session_id=session.id,
        workspace_id=session.workspace_id,
        idempotency_key="run-key",
    )
    store.reserve_run(run, ChatEvent(id="user-event", run_id=run.id, sequence=0, type="user"))
    return run


def _question(run: Run) -> QuestionRequest:
    return QuestionRequest.from_input(
        run_id=run.id,
        request_key="ask-1",
        ordinal=1,
        question="どれを選びますか？",
        options=[{"label": "A"}, {"label": "B"}, {"label": "C"}],
    )


def test_question_creation_answer_and_idempotent_resend() -> None:
    store = InMemoryChatStore()
    run = _run(store)
    question = store.create_question(_question(run))
    assert question.state is QuestionState.PENDING
    answered = store.answer_question("user", "session", run.id, question.id, "A", "answer-1")
    assert answered.answers == ["A"]
    assert (
        store.answer_question("user", "session", run.id, question.id, "A", "answer-1")
        == answered
    )
    with pytest.raises(QuestionConflictError):
        store.answer_question("user", "session", run.id, question.id, "B", "answer-2")


def test_question_contract_hides_expired_and_terminal_or_foreign_answers() -> None:
    store = InMemoryChatStore()
    run = _run(store)
    question = store.create_question(_question(run))
    with pytest.raises(QuestionOwnershipError):
        store.answer_question("other", "session", run.id, question.id, "A", "foreign")
    store.runs[run.id] = run.model_copy(update={"state": RunState.COMPLETED})
    with pytest.raises(QuestionClosedError):
        store.answer_question_for_job(run.id, question.id, "A", "late")

    expired_store = InMemoryChatStore()
    expired_run = _run(expired_store)
    expired = _question(expired_run).model_copy(
        update={"expires_at": question.created_at.replace(year=2020)}
    )
    # Keep the expired record in the same provider-neutral shape used by
    # Firestore TTL visibility checks.
    expired_store.create_question(expired)
    assert expired_store.list_questions_for_job(expired_run.id) == []
    with pytest.raises(QuestionNotFoundError):
        expired_store.answer_question_for_job(expired_run.id, expired.id, "A", "expired")


def test_interaction_state_replays_questions_and_task_updates() -> None:
    run_id = uuid4()
    events = [
        ChatEvent(
            id="pending",
            run_id=run_id,
            sequence=0,
            type="question_pending",
            payload=_question(
                Run(
                    id=run_id,
                    user_id="u",
                    session_id="s",
                    workspace_id="w",
                    idempotency_key="k",
                )
            ).model_dump(mode="json"),
        ),
        ChatEvent(
            id="create",
            run_id=run_id,
            sequence=1,
            type="tool_started",
            payload={"tool_id": "tool-1", "name": "TaskCreate", "input": {"subject": "調査"}},
        ),
        ChatEvent(
            id="created",
            run_id=run_id,
            sequence=2,
            type="tool_completed",
            payload={
                "tool_id": "tool-1",
                "content": '{"task": {"id": "T1", "subject": "調査", "status": "pending"}}',
            },
        ),
        ChatEvent(
            id="update-start",
            run_id=run_id,
            sequence=3,
            type="tool_started",
            payload={"tool_id": "tool-2", "name": "TaskUpdate", "input": {"taskId": "T1"}},
        ),
        ChatEvent(
            id="updated",
            run_id=run_id,
            sequence=4,
            type="tool_completed",
            payload={"tool_id": "tool-2", "content": {"taskId": "T1", "status": "completed"}},
        ),
    ]
    state = interaction_state(normalize_events(events))
    assert [question.id for question in state.pending_questions] == [events[0].payload["id"]]
    assert state.tasks["T1"].status == "completed"


def test_slack_number_parser_is_strict() -> None:
    question = _question(_run(InMemoryChatStore()))
    assert parse_question_answer("1", question) == ["A"]
    assert parse_question_answer("0", question) is None
    assert parse_question_answer("1,", question) is None
    assert parse_question_answer("自分で決める", question) == ["自分で決める"]


@pytest.mark.asyncio
async def test_question_broker_resumes_with_all_answers() -> None:
    store = InMemoryChatStore()
    run = _run(store)
    broker = AskUserQuestionBroker(store, run.id, poll_interval=0.01, max_wait=1)
    pending = asyncio.create_task(
        broker(
            "AskUserQuestion",
            {
                "questions": [
                    {
                        "question": "続行しますか？",
                        "header": "確認",
                        "options": [{"label": "はい"}, {"label": "いいえ"}],
                    }
                ]
            },
        )
    )
    while not store.list_questions_for_job(run.id):
        await asyncio.sleep(0.01)
    question = store.list_questions_for_job(run.id)[0]
    store.answer_question_for_job(run.id, question.id, "はい", "broker-answer")
    result = await pending
    assert result.updated_input["answers"] == {"続行しますか？": "はい"}


def test_timed_out_question_answer_starts_a_resumable_continuation() -> None:
    store = InMemoryChatStore()
    backend = InMemoryExecutionBackend()
    session = store.put_session(Session(id="session", user_id="user", workspace_id="workspace"))
    source = Run(
        user_id="user",
        session_id=session.id,
        workspace_id=session.workspace_id,
        idempotency_key="source",
    )
    store.reserve_run(
        source, ChatEvent(id="source-input", run_id=source.id, sequence=0, type="user")
    )
    question = store.create_question(_question(source))
    store.runs[source.id] = source.model_copy(update={"state": RunState.TIMED_OUT})
    store.sessions[session.id] = session.model_copy(update={"active_run_id": None})

    result = ChatService(
        ControlClient(store, backend),
        "user",
    ).continue_after_question("session", question, ["A"], idempotency_key="continuation")

    assert result.run.session_id == session.id
    assert store.list_events(result.run.id)[0].payload["content"].endswith('["A"]')


def test_slack_pending_answer_does_not_start_a_new_run() -> None:
    store = InMemorySlackThreadSessionStore()
    key = SlackThreadKey("T", "C", "1")
    app_user = application_user_id("T", "U")
    run = _run(InMemoryChatStore())
    question = _question(run)

    class SessionView:
        active_run_id = run.id

    class Service:
        def __init__(self) -> None:
            self.started = False
            self.answered: list[tuple[str, str]] = []

        def get_session(self, _session_id: str) -> SessionView:
            return SessionView()

        def pending_questions(self, _session_id: str, _run_id):
            return [question]

        def answer_question(self, _session_id, _run_id, question_id, answers, *, idempotency_key):
            self.answered.append((question_id, answers[0]))

        def start(self, *_args, **_kwargs):
            self.started = True
            raise AssertionError("pending answer must not start a run")

    service = Service()
    store.create_if_absent(key, application_user_id=app_user, session_id="session")
    posts: list[str] = []

    class Client:
        def chat_postMessage(self, **kwargs):
            posts.append(kwargs["text"])
            return kwargs

    handler = SlackMessageHandler(
        lambda _user_id: service,
        store,
        bot_user_id="BOT",
        executor=type("Direct", (), {"submit": lambda _self, function, *args: function(*args)})(),
    )
    handler.handle(
        {"user": "U", "text": "1", "channel": "C", "ts": "1", "event_id": "answer-1"},
        lambda: None,
        Client(),
        team_id="T",
    )
    assert service.answered == [(question.id, "A")]
    assert not service.started
    assert any("回答を受け付けました" in post for post in posts)

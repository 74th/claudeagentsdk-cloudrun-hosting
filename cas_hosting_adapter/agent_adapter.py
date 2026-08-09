"""Claude Agent SDK request-scoped execution boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

from .errors import AgentError, AgentQuestionTimeoutError
from .models import QuestionOption, QuestionRequest, QuestionState
from .protocols import ChatStore

LOGGER = logging.getLogger(__name__)


class AskUserQuestionBroker:
    """Persist AskUserQuestion requests and wait for durable answers."""

    def __init__(
        self,
        store: ChatStore,
        run_id: UUID,
        *,
        poll_interval: float = 0.25,
        max_wait: float | None = None,
    ) -> None:
        self._store = store
        self._run_id = run_id
        self._poll_interval = max(0.01, poll_interval)
        self._max_wait = max_wait
        self._question_calls = 0
        self.timed_out = False

    @staticmethod
    def _questions(tool_input: Mapping[str, Any]) -> list[dict[str, Any]]:
        raw = tool_input.get("questions")
        if not isinstance(raw, list) or not 1 <= len(raw) <= 4:
            raise ValueError("AskUserQuestion must contain between 1 and 4 questions")
        result: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("AskUserQuestion entries must be objects")
            question = item.get("question")
            options = item.get("options")
            if not isinstance(question, str) or not question.strip():
                raise ValueError("AskUserQuestion question must not be blank")
            if not isinstance(options, list) or not 2 <= len(options) <= 4:
                raise ValueError("AskUserQuestion must contain 2 to 4 options")
            labels: set[str] = set()
            normalized_options: list[dict[str, str]] = []
            for option in options:
                if not isinstance(option, dict):
                    raise ValueError("AskUserQuestion options must be objects")
                label = option.get("label")
                if not isinstance(label, str) or not label.strip() or label in labels:
                    raise ValueError("AskUserQuestion option labels must be unique")
                labels.add(label)
                normalized_options.append(
                    {
                        "label": label.strip(),
                        "description": str(option.get("description") or ""),
                    }
                )
            result.append(
                {
                    "question": question.strip(),
                    "header": str(item.get("header") or ""),
                    "options": normalized_options,
                    "multiSelect": bool(item.get("multiSelect", False)),
                }
            )
        return result

    async def __call__(
        self, tool_name: str, tool_input: dict[str, Any], context: Any = None
    ) -> Any:
        from claude_agent_sdk import PermissionResultAllow

        if tool_name != "AskUserQuestion":
            return PermissionResultAllow()
        try:
            values = self._questions(tool_input)
            tool_id = getattr(context, "tool_use_id", None) or getattr(context, "tool_id", None)
            if tool_id:
                request_key = str(tool_id)
            else:
                self._question_calls += 1
                request_key = f"{self._question_calls}:{self._canonical_key(values)}"
            questions = [
                QuestionRequest.from_input(
                    run_id=self._run_id,
                    request_key=request_key,
                    ordinal=index,
                    question=value["question"],
                    header=value["header"],
                    options=[QuestionOption(**option) for option in value["options"]],
                    multi_select=value["multiSelect"],
                )
                for index, value in enumerate(values, 1)
            ]
            created = self._store.create_questions(self._run_id, questions)
        except (ValueError, TypeError) as error:
            LOGGER.warning("claude_sdk.ask_user_question.invalid run_id=%s", self._run_id)
            raise AgentError("invalid AskUserQuestion payload") from error
        loop = asyncio.get_running_loop()
        started = loop.time()
        while True:
            current = await asyncio.to_thread(self._store.list_questions_for_job, self._run_id)
            by_id = {question.id: question for question in current}
            if all(
                by_id.get(question.id, question).state is QuestionState.ANSWERED
                for question in created
            ):
                answers: dict[str, Any] = {}
                for question in created:
                    answered = by_id[question.id]
                    answer_values = answered.answers or []
                    answers[question.question] = (
                        answer_values if question.multi_select else answer_values[0]
                    )
                return PermissionResultAllow(
                    updated_input={"questions": tool_input["questions"], "answers": answers}
                )
            if self._max_wait is not None and loop.time() - started >= self._max_wait:
                self.timed_out = True
                raise AgentQuestionTimeoutError("AskUserQuestion answer timed out")
            await asyncio.sleep(self._poll_interval)

    @staticmethod
    def _canonical_key(values: list[dict[str, Any]]) -> str:
        import json

        return "ask-user:" + hashlib.sha256(
            json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class RestoredTranscriptSessionStore:
    """Expose a restored on-disk Claude transcript through the SDK resume API."""

    def __init__(self, transcript_dir: Path) -> None:
        self._transcript_dir = transcript_dir

    async def append(self, _key: Any, _entries: list[dict[str, Any]]) -> None:
        # Claude Code already writes the authoritative local JSONL.  It is
        # included in the immutable workspace snapshot after the run.
        return None

    async def load(self, key: dict[str, Any]) -> list[dict[str, Any]] | None:
        session_id = key.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return None
        candidates = sorted(
            (self._transcript_dir / ".claude" / "projects").glob(f"**/{session_id}.jsonl")
        )
        if not candidates:
            LOGGER.warning("claude_sdk.session_store.missing session_id=%s", session_id)
            return None
        try:
            lines = candidates[0].read_text(encoding="utf-8").splitlines()
            entries = [json.loads(line) for line in lines]
        except (OSError, json.JSONDecodeError):
            LOGGER.exception("claude_sdk.session_store.load_failed session_id=%s", session_id)
            return None
        if not all(isinstance(entry, dict) for entry in entries):
            LOGGER.warning("claude_sdk.session_store.invalid session_id=%s", session_id)
            return None
        LOGGER.info(
            "claude_sdk.session_store.loaded session_id=%s entries=%d",
            session_id,
            len(entries),
        )
        return entries


class ClaudeAgentAdapter:
    def __init__(
        self,
        agent: Any | None = None,
        *,
        model: str,
        question_store: ChatStore | None = None,
        run_id: UUID | None = None,
        question_timeout: float | None = None,
    ) -> None:
        # Kept only for source compatibility with the previous factory.  The SDK
        # module is imported inside events() so each JobRunner process owns its
        # Claude session files and no provider client escapes this boundary.
        self.agent = agent
        self.model = model
        self.question_store = question_store
        self.run_id = run_id
        self.question_timeout = question_timeout

    def _options(
        self,
        *,
        workspace: Path,
        transcript_dir: Path,
        resume: str | None,
        can_use_tool: Any | None = None,
    ) -> dict[str, Any]:
        transcript_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        # Claude CLI stores its session state below HOME.  transcript_dir is
        # created from the run-scoped temporary directory by JobRunner.
        env["HOME"] = str(transcript_dir.resolve())
        options: dict[str, Any] = {
            "cwd": str(workspace.resolve()),
            "model": self.model,
            "env": env,
            # bypassPermissions shadows can_use_tool in the SDK. When the
            # question broker is active, route permissions through the broker
            # so AskUserQuestion can actually suspend the session.
            "permission_mode": "default" if can_use_tool is not None else "bypassPermissions",
        }
        if resume is not None:
            options["resume"] = resume
            # `resume` normally resolves a transcript by a cwd-derived path.
            # Cloud Run Jobs have a different temporary cwd on every run, so
            # also provide the restored JSONL via the SDK's path-independent
            # session-store interface.
            options["session_store"] = RestoredTranscriptSessionStore(transcript_dir)
        if can_use_tool is not None:
            options["can_use_tool"] = can_use_tool
        return options

    @staticmethod
    def _event_id(
        *, message: Any, position: int, part: int, event_type: str, payload: dict[str, Any]
    ) -> str:
        message_id = getattr(message, "uuid", None)
        if isinstance(message_id, str) and message_id:
            return f"sdk:{message_id}:{part}"
        material = json.dumps(
            {"position": position, "part": part, "type": event_type, "payload": payload},
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        return f"sdk:{hashlib.sha256(material.encode()).hexdigest()}"

    def _normalise_message(self, message: Any, *, position: int) -> list[dict[str, Any]]:
        """Convert SDK objects into the durable event vocabulary."""
        name = type(message).__name__
        raw: list[tuple[str, dict[str, Any]]] = []
        if name == "UserMessage":
            content = getattr(message, "content", "")
            if isinstance(content, str):
                raw.append(("user", {"content": content}))
            else:
                for block in content:
                    normalised = self._normalise_content_block(block, text_type="user")
                    if normalised is not None:
                        raw.append(normalised)
        elif name == "AssistantMessage":
            for block in getattr(message, "content", []):
                normalised = self._normalise_content_block(block, text_type="agent")
                if normalised is not None:
                    raw.append(normalised)
            if getattr(message, "error", None):
                raw.append(("error", {"code": message.error}))
        elif name in {"TaskProgressMessage", "TaskStartedMessage", "TaskUpdatedMessage"}:
            raw.append(
                (
                    "progress",
                    {
                        "task_id": getattr(message, "task_id", None),
                        "description": getattr(message, "description", None),
                        "status": getattr(message, "status", None),
                    },
                )
            )
        elif name in {"MirrorErrorMessage", "HookEventMessage"}:
            raw.append(("error", {"message": str(getattr(message, "error", name))}))
        else:
            raw.append(("progress", {"raw_type": name}))
        events: list[dict[str, Any]] = []
        session_id = getattr(message, "session_id", None)
        for part, (event_type, payload) in enumerate(raw):
            event: dict[str, Any] = {
                "event_id": self._event_id(
                    message=message,
                    position=position,
                    part=part,
                    event_type=event_type,
                    payload=payload,
                ),
                "event_type": event_type,
                "payload": payload,
            }
            if isinstance(session_id, str) and session_id:
                event["claude_session_id"] = session_id
            events.append(event)
        return events

    @staticmethod
    def _normalise_content_block(
        block: Any, *, text_type: str
    ) -> tuple[str, dict[str, Any]] | None:
        """Map SDK content blocks to the durable event vocabulary."""
        block_name = type(block).__name__
        if block_name == "TextBlock":
            return text_type, {"content": getattr(block, "text", "")}
        if block_name in {"ToolUseBlock", "ServerToolUseBlock"}:
            return "tool_started", {
                "tool_id": getattr(block, "id", ""),
                "name": getattr(block, "name", ""),
                "input": getattr(block, "input", {}),
            }
        if block_name in {"ToolResultBlock", "ServerToolResultBlock"}:
            return "tool_completed", {
                "tool_id": getattr(block, "tool_use_id", ""),
                "content": getattr(block, "content", None),
                "is_error": bool(getattr(block, "is_error", False)),
            }
        return None

    async def events(
        self, *, prompt: str, workspace: Path, transcript_dir: Path, resume: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield normalized SDK messages without mutating process-global environment."""
        try:
            from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
            try:
                from claude_agent_sdk import ClaudeSDKClient
            except ImportError:
                ClaudeSDKClient = None  # type: ignore[assignment,misc]
        except ImportError as error:
            raise AgentError("claude-agent-sdk is not installed") from error
        broker = None
        if self.question_store is not None and self.run_id is not None:
            broker = AskUserQuestionBroker(
                self.question_store, self.run_id, max_wait=self.question_timeout
            )
        options = self._options(
            workspace=workspace,
            transcript_dir=transcript_dir,
            resume=resume,
            can_use_tool=broker,
        )
        LOGGER.info(
            "claude_sdk.query.start model=%s resume=%s",
            self.model,
            resume is not None,
        )
        try:
            async def messages() -> AsyncIterator[Any]:
                sdk_options = ClaudeAgentOptions(**options)
                if ClaudeSDKClient is not None:
                    # A client owns the bidirectional session and keeps the
                    # can_use_tool callback alive while an answer is pending.
                    client = ClaudeSDKClient(options=sdk_options)
                    prompt_done = asyncio.Event()
                    try:
                        initial_prompt: Any = prompt
                        if broker is not None:
                            # Keep stdin open while a permission callback may
                            # be waiting for a durable answer. Query's
                            # streaming-input task closes stdin as soon as
                            # this iterable ends; a one-shot iterable causes
                            # the CLI to abort can_use_tool with "Stream
                            # closed" before the UI can answer.
                            initial_prompt = self._prompt_stream(prompt, prompt_done)
                        await client.connect(initial_prompt)
                        async for message in client.receive_response():
                            yield message
                    finally:
                        prompt_done.set()
                        await client.disconnect()
                    return
                async for message in query(prompt=prompt, options=sdk_options):
                    yield message

            position = 0
            async for message in messages():
                if isinstance(message, ResultMessage):
                    LOGGER.info(
                        "claude_sdk.query.result position=%d is_error=%s",
                        position,
                        message.is_error,
                    )
                    event_type = "error" if message.is_error else "final"
                    payload = {"output": message.result}
                    yield {
                        "event_id": self._event_id(
                            message=message,
                            position=position,
                            part=0,
                            event_type=event_type,
                            payload=payload,
                        ),
                        "event_type": event_type,
                        "claude_session_id": message.session_id,
                        "payload": payload,
                    }
                else:
                    events = self._normalise_message(message, position=position)
                    LOGGER.info(
                        "claude_sdk.message position=%d message_type=%s normalized_events=%d",
                        position,
                        type(message).__name__,
                        len(events),
                    )
                    for event in events:
                        yield event
                position += 1
            if broker is not None and broker.timed_out:
                raise AgentQuestionTimeoutError("AskUserQuestion answer timed out")
        except AgentError:
            raise
        except Exception as error:
            LOGGER.exception("claude_sdk.query.failed model=%s", self.model)
            raise AgentError("Claude Agent SDK invocation failed") from error

    @staticmethod
    async def _prompt_stream(
        prompt: str, done: asyncio.Event | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Provide a streaming envelope and keep stdin open until the run ends."""
        yield {
            "type": "user",
            "message": {"role": "user", "content": prompt},
            "parent_tool_use_id": None,
            "session_id": "default",
        }
        if done is not None:
            await done.wait()

    async def run(
        self,
        *,
        prompt: str,
        workspace: Path,
        transcript_dir: Path,
        resume: str | None = None,
    ) -> str:
        """Run either a new query or a resumed Claude session via one path."""
        async for event in self.events(
            prompt=prompt,
            workspace=workspace,
            transcript_dir=transcript_dir,
            resume=resume,
        ):
            if event["event_type"] == "final":
                output = event["payload"].get("output")
                if isinstance(output, str):
                    return output
                raise AgentError("Claude Agent SDK returned a non-text terminal result")
        raise AgentError("Claude Agent SDK ended without a terminal result")

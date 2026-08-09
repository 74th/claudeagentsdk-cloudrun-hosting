"""Claude Agent SDK request-scoped execution boundary."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

from .errors import AgentError, AgentQuestionTimeoutError
from .job_runner import ExecutionLimits, JobInvocation, JobRunner
from .models import QuestionOption, QuestionRequest, QuestionState, RunState
from .protocols import ChatStore
from .runtime import (
    AgentExecutionResult,
    ClaudeAgentConfig,
    RuntimePolicy,
    WorkspaceInitializer,
    WorkspaceSetup,
)
from .workspace_store import (
    StoragePaths,
    create_workspace_snapshot,
    request_directories,
)

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
            source = candidates[0].read_bytes()
            if any(candidate.read_bytes() != source for candidate in candidates[1:]):
                raise AgentError("multiple restored transcripts conflict for the resume session")
            lines = source.decode("utf-8").splitlines()
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


class _ClaudeSessionExecutor:
    """Private SDK session seam used by the durable adapter facade."""

    def __init__(self, adapter: ClaudeAgentAdapter) -> None:
        self._adapter = adapter

    async def events(
        self, *, prompt: str, workspace: Path, transcript_dir: Path, resume: str | None
    ) -> AsyncIterator[dict[str, Any]]:
        async for event in self._adapter._events(
            prompt=prompt,
            workspace=workspace,
            transcript_dir=transcript_dir,
            resume=resume,
        ):
            yield event


class ClaudeAgentAdapter:
    def __init__(
        self,
        agent: Any | None = None,
        *,
        model: str | None = None,
        agent_config: ClaudeAgentConfig | None = None,
        chat_store: ChatStore | None = None,
        workspace_store: Any | None = None,
        runtime_policy: RuntimePolicy | None = None,
        workspace_initializer: WorkspaceInitializer | None = None,
        workspace_setup: WorkspaceSetup | None = None,
        question_store: ChatStore | None = None,
        run_id: UUID | None = None,
        question_timeout: float | None = None,
    ) -> None:
        # Kept only for source compatibility with the previous factory.  The SDK
        # module is imported inside events() so each JobRunner process owns its
        # Claude session files and no provider client escapes this boundary.
        self.agent = agent
        if agent_config is not None and model is not None and model != agent_config.model:
            raise ValueError("model must match agent_config.model")
        self.agent_config = agent_config or ClaudeAgentConfig(
            model=model or "claude-haiku-4-5@20251001"
        )
        self.model = self.agent_config.model
        self.chat_store = chat_store or question_store
        self.workspace_store = workspace_store
        self.runtime_policy = runtime_policy or RuntimePolicy(
            question_timeout=question_timeout
        )
        if question_timeout is not None and runtime_policy is not None:
            self.runtime_policy = RuntimePolicy(
                snapshot_max_bytes=runtime_policy.snapshot_max_bytes,
                question_timeout=question_timeout,
                sdk_version=runtime_policy.sdk_version,
                snapshot_schema_version=runtime_policy.snapshot_schema_version,
                max_runtime=runtime_policy.max_runtime,
                idle_timeout=runtime_policy.idle_timeout,
                log_level=runtime_policy.log_level,
            )
        self.workspace_initializer = workspace_initializer
        self.workspace_setup = workspace_setup
        self.question_store = self.chat_store
        self.run_id = run_id
        self.question_timeout = self.runtime_policy.question_timeout
        self._session_executor = _ClaudeSessionExecutor(self)

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
        if self.agent_config.system_prompt:
            options["system_prompt"] = self.agent_config.system_prompt
        if self.agent_config.allowed_tools:
            options["allowed_tools"] = list(self.agent_config.allowed_tools)
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
    def _workspace_key(workspace: Path) -> str:
        """Return Claude Code's portable project directory key."""

        return str(workspace.resolve()).replace("/", "-")

    def _prepare_transcript_resume(
        self, transcript_dir: Path, workspace: Path, session_id: str
    ) -> None:
        """Make only the requested restored transcript visible at the new cwd.

        Temporary workspace paths change for every Cloud Run Job.  Older SDK
        versions resolve a session through a cwd-derived project directory, so
        the adapter maps the single requested JSONL rather than copying every
        transcript in a snapshot.
        """

        if not session_id.strip():
            raise AgentError("resume session ID must not be blank")
        projects = transcript_dir / ".claude" / "projects"
        destination_dir = projects / self._workspace_key(workspace)
        candidates = sorted(projects.glob(f"**/{session_id}.jsonl"))
        if not candidates:
            LOGGER.info("claude_sdk.transcript.not_found session_id=%s", session_id)
            return
        source = candidates[0]
        source_data = source.read_bytes()
        if any(candidate.read_bytes() != source_data for candidate in candidates[1:]):
            raise AgentError("multiple restored transcripts conflict for the resume session")
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / source.name
        if source == destination:
            return
        if destination.exists():
            if destination.read_bytes() != source_data:
                raise AgentError("resume transcript destination conflicts with restored session")
            return
        shutil.copy2(source, destination)

    async def run_job(self, invocation: JobInvocation) -> int:
        """Run one claimed durable job and return its process exit code.

        All Store mutations happen here or in the private JobRunner seam.  The
        application callback receives only a workspace path.
        """

        if self.chat_store is None or self.workspace_store is None:
            raise ValueError("chat_store and workspace_store are required for run_job")
        runner = JobRunner(self.chat_store)
        claimed = runner.claim(invocation)
        if claimed is None:
            return 0
        result: AgentExecutionResult | None = None
        try:
            prompt = runner.prompt_for_run(invocation.run_id)
            session = self.chat_store.get_session(claimed.user_id, claimed.session_id)
            with request_directories() as directories:
                if session.snapshot is not None:
                    # Existing snapshots contain the archive created by the
                    # WorkspaceStore.  Manifest validation remains available
                    # to callers using the stricter helper; job snapshots use
                    # the unchanged provider-neutral reference format.
                    data = self.workspace_store.get(session.snapshot)
                    archive = directories.root / "resume.tar.gz"
                    archive.write_bytes(data)
                    try:
                        from .workspace_store import extract_snapshot

                        extract_snapshot(
                            archive,
                            directories,
                            max_bytes=self.runtime_policy.snapshot_max_bytes,
                        )
                    finally:
                        archive.unlink(missing_ok=True)
                elif self.workspace_initializer is not None:
                    self.workspace_initializer(directories.workspace)
                if self.workspace_setup is not None:
                    self.workspace_setup(directories.workspace)

                resume = session.claude_session_id
                if resume is not None:
                    self._prepare_transcript_resume(
                        directories.claude_session, directories.workspace, resume
                    )
                execution = ClaudeAgentAdapter(
                    agent_config=self.agent_config,
                    chat_store=self.chat_store,
                    runtime_policy=self.runtime_policy,
                    run_id=invocation.run_id,
                )
                limits = None
                if self.runtime_policy.max_runtime and self.runtime_policy.idle_timeout:
                    limits = ExecutionLimits(
                        self.runtime_policy.max_runtime, self.runtime_policy.idle_timeout
                    )
                try:
                    state = await runner.persist_events(
                        invocation,
                        execution.events(
                            prompt=prompt,
                            workspace=directories.workspace,
                            transcript_dir=directories.claude_session,
                            resume=resume,
                        ),
                        claimed_run=claimed,
                        limits=limits,
                    )
                    if state is RunState.TIMED_OUT:
                        result = AgentExecutionResult(
                            "timed_out",
                            claude_session_id=runner.claude_session_id,
                            error_code="timeout",
                            snapshot_required=True,
                        )
                    elif state is RunState.CANCELLED:
                        result = AgentExecutionResult(
                            "cancelled",
                            claude_session_id=runner.claude_session_id,
                            error_code="cancelled",
                        )
                    else:
                        result = AgentExecutionResult(
                            "completed",
                            output=runner.result_for_run(invocation.run_id),
                            claude_session_id=runner.claude_session_id,
                            snapshot_required=True,
                        )
                except AgentQuestionTimeoutError:
                    result = AgentExecutionResult(
                        "timed_out",
                        claude_session_id=runner.claude_session_id,
                        error_code="question_timeout",
                        snapshot_required=True,
                    )

                snapshot = None
                if result.snapshot_required:
                    current = self.chat_store.get_run_for_job(invocation.run_id)
                    paths = StoragePaths.for_session(
                        user_id=current.user_id,
                        session_id=current.session_id,
                        schema_version=current.schema_version,
                        sdk_version=self.runtime_policy.sdk_version,
                    )
                    snapshot, _manifest = create_workspace_snapshot(
                        self.workspace_store,
                        object_key=paths.snapshot_path(invocation.run_id),
                        source=directories,
                        run_id=invocation.run_id,
                        sdk_version=self.runtime_policy.sdk_version,
                        max_bytes=self.runtime_policy.snapshot_max_bytes,
                    )
                if result.status == "completed":
                    if snapshot is None:
                        raise AgentError("completed execution has no workspace snapshot")
                    runner.commit_success(
                        invocation,
                        result=result.output or "",
                        snapshot=snapshot,
                        claude_session_id=result.claude_session_id,
                    )
                else:
                    runner.commit_unsuccessful(
                        invocation,
                        {
                            "cancelled": RunState.CANCELLED,
                            "timed_out": RunState.TIMED_OUT,
                        }[result.status],
                        error_code=result.error_code,
                        snapshot=snapshot,
                        claude_session_id=result.claude_session_id,
                    )
        except Exception as error:
            LOGGER.exception("job.lifecycle.failed run_id=%s", invocation.run_id)
            try:
                runner.commit_unsuccessful(
                    invocation, state=RunState.FAILED,
                    error_code=getattr(error, "code", "job_failed"),
                )
            except Exception:
                LOGGER.exception("job.lifecycle.failure_commit_failed run_id=%s", invocation.run_id)
            return 1
        return 0 if result is not None and result.status == "completed" else 1

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

    async def _events(
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
        if resume is not None:
            self._prepare_transcript_resume(transcript_dir, workspace, resume)
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

    async def events(
        self, *, prompt: str, workspace: Path, transcript_dir: Path, resume: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Deprecated low-level test seam; jobs use :meth:`run_job`.

        Compatibility is retained until 2026-09-30 for repository tests and
        provider experiments. Application code should not compose this stream
        with JobRunner directly.
        """

        async for event in self._session_executor.events(
            prompt=prompt,
            workspace=workspace,
            transcript_dir=transcript_dir,
            resume=resume,
        ):
            yield event

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
        """Deprecated SDK test seam; use :meth:`run_job` for durable runs.

        This compatibility wrapper is scheduled for removal on 2026-09-30.
        """
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

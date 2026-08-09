"""Claude Agent SDK request-scoped execution boundary."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from .errors import AgentError

LOGGER = logging.getLogger(__name__)


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
    def __init__(self, agent: Any | None = None, *, model: str) -> None:
        # Kept only for source compatibility with the previous factory.  The SDK
        # module is imported inside events() so each JobRunner process owns its
        # Claude session files and no provider client escapes this boundary.
        self.agent = agent
        self.model = model

    def _options(
        self, *, workspace: Path, transcript_dir: Path, resume: str | None
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
            "permission_mode": "bypassPermissions",
        }
        if resume is not None:
            options["resume"] = resume
            # `resume` normally resolves a transcript by a cwd-derived path.
            # Cloud Run Jobs have a different temporary cwd on every run, so
            # also provide the restored JSONL via the SDK's path-independent
            # session-store interface.
            options["session_store"] = RestoredTranscriptSessionStore(transcript_dir)
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
        return [
            {
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
            for part, (event_type, payload) in enumerate(raw)
        ]

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
        except ImportError as error:
            raise AgentError("claude-agent-sdk is not installed") from error
        options = self._options(workspace=workspace, transcript_dir=transcript_dir, resume=resume)
        LOGGER.info(
            "claude_sdk.query.start model=%s resume=%s",
            self.model,
            resume is not None,
        )
        try:
            position = 0
            async for message in query(prompt=prompt, options=ClaudeAgentOptions(**options)):
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
        except Exception as error:
            LOGGER.exception("claude_sdk.query.failed model=%s", self.model)
            raise AgentError("Claude Agent SDK invocation failed") from error

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

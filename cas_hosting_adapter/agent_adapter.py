"""Claude Agent SDK request-scoped execution boundary."""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from .errors import AgentError


class ClaudeAgentAdapter:
    def __init__(self, agent: Any, *, model: str) -> None:
        self.agent = agent
        self.model = model

    async def events(
        self, *, prompt: str, workspace: Path, transcript_dir: Path, resume: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield normalized SDK messages without mutating process-global environment."""
        try:
            from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
        except ImportError as error:
            raise AgentError("claude-agent-sdk is not installed") from error
        env = dict(os.environ)
        env["HOME"] = str(transcript_dir)
        options: dict[str, Any] = {
            "cwd": str(workspace),
            "model": self.model,
            "env": env,
            "permission_mode": "bypassPermissions",
        }
        if resume is not None:
            options["resume"] = resume
        try:
            async for message in query(prompt=prompt, options=ClaudeAgentOptions(**options)):
                if isinstance(message, ResultMessage):
                    yield {
                        "event_type": "terminal",
                        "claude_session_id": message.session_id,
                        "output": message.result,
                    }
                else:
                    yield {"event_type": "progress", "raw_type": type(message).__name__}
        except Exception as error:
            raise AgentError("Claude Agent SDK invocation failed") from error

"""Small Cloud Run Job composition root for the sample agent."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from cas_hosting_adapter import ClaudeAgentConfig
from cas_hosting_adapter.factory import create_google_cloud_job_composition

AGENT = ClaudeAgentConfig(
    system_prompt=(
        "You are a helpful repository assistant. Keep changes focused and explain "
        "the result clearly."
    ),
    model=os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5@20251001"),
    allowed_tools=("Read", "Write", "Edit", "Bash", "AskUserQuestion"),
)


def setup_workspace(workspace: Path) -> None:
    """Prepare run-specific files after initialization or snapshot restore.

    The hook is intentionally idempotent because it runs for every job,
    including resumed jobs.
    """

    (workspace / ".agent-runtime").mkdir(parents=True, exist_ok=True)


async def run() -> int:
    composition = create_google_cloud_job_composition()
    return await composition.run_from_environment(
        AGENT,
        workspace_setup=setup_workspace,
    )


def main() -> int:
    return asyncio.run(run())

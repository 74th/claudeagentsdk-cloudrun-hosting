"""Small Cloud Run Job composition root for the sample agent."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from cas_hosting_adapter import AgentUsageRecord, ClaudeAgentConfig
from cas_hosting_adapter.factory import create_google_cloud_job_composition

AGENT = ClaudeAgentConfig(
    system_prompt=(
        "You are a helpful repository assistant. Keep changes focused and explain "
        "the result clearly."
    ),
    model=os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5@20251001"),
    allowed_tools=("Read", "Write", "Edit", "Bash", "AskUserQuestion"),
)
LOGGER = logging.getLogger(__name__)


def log_agent_usage(record: AgentUsageRecord) -> None:
    LOGGER.info("agent_usage %s", json.dumps({
        "user_name": record.user_name, "run_id": str(record.run_id),
        "session_name": record.session_name, "estimated_cost_usd": record.estimated_cost_usd,
        "recorded_at": record.recorded_at.isoformat(), "duration_ms": record.duration_ms,
    }, ensure_ascii=False, separators=(",", ":")))


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
        usage_hook=log_agent_usage,
    )


def main() -> int:
    return asyncio.run(run())

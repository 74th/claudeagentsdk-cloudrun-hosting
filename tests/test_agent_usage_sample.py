import json
import logging
from datetime import UTC, datetime
from uuid import uuid4

from cas_hosting_adapter import AgentUsageRecord
from example.agent.runtime import log_agent_usage


def test_sample_usage_hook_logs_one_json_compatible_record(caplog) -> None:
    record = AgentUsageRecord(
        user_name="user@example.com",
        run_id=uuid4(),
        session_name="Repository task",
        estimated_cost_usd=None,
        recorded_at=datetime(2026, 8, 11, 12, 34, 56, tzinfo=UTC),
        duration_ms=None,
    )
    caplog.set_level(logging.INFO, logger="example.agent.runtime")

    log_agent_usage(record)

    message = caplog.records[-1].getMessage()
    assert message.startswith("agent_usage ")
    payload = json.loads(message.removeprefix("agent_usage "))
    assert payload == {
        "user_name": "user@example.com",
        "run_id": str(record.run_id),
        "session_name": "Repository task",
        "estimated_cost_usd": None,
        "recorded_at": "2026-08-11T12:34:56+00:00",
        "duration_ms": None,
    }

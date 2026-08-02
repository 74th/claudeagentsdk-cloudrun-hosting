"""PoC runtime for verifying the Agent Platform custom-container contract."""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="test-claude-agent-sample")
MODEL = "claude-haiku-4-5@20251001"


class RuntimeRequest(BaseModel):
    class_method: str
    input: dict[str, Any] = {}


async def invoke(message: object) -> str:
    if not isinstance(message, str) or not message.strip():
        raise HTTPException(400, "input.message must be a non-empty string")
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

    env = dict(os.environ)
    env.update({"CLAUDE_CODE_USE_VERTEX": "1", "ANTHROPIC_VERTEX_PROJECT_ID": env["ANTHROPIC_VERTEX_PROJECT_ID"], "CLOUD_ML_REGION": "global"})
    options = ClaudeAgentOptions(cwd="/workspace", model=MODEL, allowed_tools=[], env=env)
    async for event in query(prompt=message.strip(), options=options):
        if isinstance(event, ResultMessage) and isinstance(event.result, str):
            return event.result
    raise HTTPException(502, "Claude Agent SDK returned no final response")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


def normalize(request: RuntimeRequest | str) -> RuntimeRequest:
    return request if isinstance(request, RuntimeRequest) else RuntimeRequest.model_validate_json(request)


@app.post("/api/reasoning_engine")
async def reasoning_engine(request: RuntimeRequest | str) -> dict[str, str]:
    request = normalize(request)
    if request.class_method not in {"query", "async_query"}:
        raise HTTPException(400, "unsupported class_method")
    return {"output": await invoke(request.input.get("message"))}


@app.post("/api/stream_reasoning_engine")
async def stream_reasoning_engine(request: RuntimeRequest | str) -> StreamingResponse:
    request = normalize(request)
    if request.class_method not in {"stream_query", "async_stream_query"}:
        raise HTTPException(400, "unsupported class_method")

    async def stream() -> AsyncIterator[str]:
        yield json.dumps({"output": await invoke(request.input.get("message"))}) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")

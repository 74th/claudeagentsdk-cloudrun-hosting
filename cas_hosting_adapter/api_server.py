"""Agent Platform runtime boundary."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ValidationError

from .models import HostingSettings


class RuntimeRequest(BaseModel):
    class_method: str
    input: dict[str, Any] | None = None


def _normalize(request: RuntimeRequest | str) -> RuntimeRequest:
    if isinstance(request, RuntimeRequest):
        return request
    try:
        return RuntimeRequest.model_validate_json(request)
    except ValidationError as error:
        raise HTTPException(400, "invalid Agent Platform request") from error


def create_app(
    agent: Callable[[str], Awaitable[str]],
    settings: HostingSettings,
    workspace_initializer: Any = None,
) -> FastAPI:
    """Create the Agent Platform-compatible ASGI app."""
    del workspace_initializer
    app = FastAPI(title="CAS Hosting Adapter")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    async def invoke(request: RuntimeRequest | str, expected_method: str) -> dict[str, str]:
        normalized = _normalize(request)
        if normalized.class_method != expected_method:
            raise HTTPException(400, "unsupported class_method")
        payload = normalized.input or {}
        user_id = payload.get("user_id")
        message = payload.get("message")
        if not isinstance(user_id, str) or not user_id.strip():
            raise HTTPException(400, "input.user_id is required")
        if (
            not isinstance(message, str)
            or not message.strip()
            or len(message) > settings.max_message_chars
        ):
            raise HTTPException(400, "input.message is invalid")
        return {"run_id": str(uuid4()), "output": await agent(message)}

    @app.post("/api/reasoning_engine")
    async def reasoning_engine(request: RuntimeRequest | str) -> dict[str, str]:
        return await invoke(request, "query")

    @app.post("/api/stream_reasoning_engine")
    async def stream_reasoning_engine(request: RuntimeRequest | str) -> StreamingResponse:
        response = await invoke(request, "stream_query")

        async def stream() -> AsyncIterator[str]:
            yield json.dumps(response) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    return app

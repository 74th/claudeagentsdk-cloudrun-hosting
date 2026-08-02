"""Minimal custom-container control for Agent Platform async dispatch."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="agent-platform-async-echo")
logger = logging.getLogger("async-echo")


class RuntimeRequest(BaseModel):
    class_method: str
    input: dict[str, Any]


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/reasoning_engine")
async def reasoning_engine(request: RuntimeRequest | str) -> dict[str, Any]:
    request = request if isinstance(request, RuntimeRequest) else RuntimeRequest.model_validate_json(request)
    logger.warning("received method=%s input=%s", request.class_method, request.input)
    if request.class_method not in {"query", "async_query"}:
        raise HTTPException(400, "unsupported class_method")
    return {"output": {"method": request.class_method, "input": request.input}}

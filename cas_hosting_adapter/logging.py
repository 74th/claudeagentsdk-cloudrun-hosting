"""Structured invocation logging with an explicit DEBUG payload boundary."""
from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any, cast


class InvocationLogger(logging.LoggerAdapter[logging.Logger]):
    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        extra = dict(cast(MutableMapping[str, Any] | None, kwargs.get("extra")) or {})
        extra.update(cast(MutableMapping[str, Any], self.extra))
        kwargs["extra"] = extra
        return msg, kwargs

    def debug_payload(self, label: str, payload: Any) -> None:
        if self.logger.isEnabledFor(logging.DEBUG):
            self.debug("%s=%r", label, payload)


def invocation_logger(
    *, user_id: str, session_id: str | None = None, run_id: str | None = None,
    execution_name: str | None = None,
    error_code: str | None = None,
) -> InvocationLogger:
    return InvocationLogger(
        logging.getLogger("cas_hosting_adapter"),
        {"user_id": user_id, "session_id": session_id, "run_id": run_id,
         "execution_name": execution_name, "error_code": error_code},
    )

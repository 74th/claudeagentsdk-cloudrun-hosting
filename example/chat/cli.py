"""会話ストリームを JSON Lines で確認する CLI。"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from cas_hosting_adapter.errors import HostingError
from cas_hosting_adapter.models import RunState

from .service import ChatService, create_control_client_from_release_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stream a Claude chat run as JSON Lines")
    parser.add_argument("--release-config", "--release", required=True, type=Path)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--session-id")
    parser.add_argument("--idempotency-key")
    return parser


def _write_record(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def _safe_error(error: BaseException) -> tuple[str, str]:
    if isinstance(error, HostingError):
        return error.code, "会話サービスの処理に失敗しました"
    if isinstance(error, (ValueError, FileNotFoundError)):
        return "validation", "引数または release config が不正です"
    return "internal", "会話サービスで予期しないエラーが発生しました"


def run_cli(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        service = ChatService(
            create_control_client_from_release_config(args.release_config), args.user_id
        )
        started = service.start(
            args.prompt,
            session_id=args.session_id,
            idempotency_key=args.idempotency_key,
        )
        _write_record(
            {
                "type": "start",
                "session_id": started.session_id,
                "run_id": started.run_id,
            }
        )
        for event in service.stream(started.run):
            _write_record(
                {
                    "type": "event",
                    "event": {
                        "id": event.id,
                        "run_id": event.run_id,
                        "sequence": event.sequence,
                        "event_type": event.event_type,
                        "kind": event.kind,
                        "content": event.content,
                        "payload": event.payload,
                    },
                }
            )
        final_run = service.get_run(started.session_id, started.run_id)
        if final_run.state in {
            RunState.FAILED,
            RunState.CANCELLED,
            RunState.TIMED_OUT,
            RunState.DISPATCH_FAILED,
            RunState.PERSISTENCE_FAILED,
        }:
            print(f"run finished with state={final_run.state.value}", file=sys.stderr)
            return 1
        return 0
    except Exception as error:
        code, message = _safe_error(error)
        print(f"error[{code}]: {message}", file=sys.stderr)
        return 2


def main(argv: Sequence[str] | None = None) -> int:
    return run_cli(argv)


if __name__ == "__main__":
    raise SystemExit(main())

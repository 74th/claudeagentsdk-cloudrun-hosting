"""Slack Socket Mode の起動エントリーポイント。"""

from __future__ import annotations

import argparse
from pathlib import Path

from example.chat import ChatService

from .config import SlackBotSettings, create_slack_dependencies
from .handler import SlackMessageHandler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Claude Slack Socket Mode bot")
    parser.add_argument("--release-config", required=True, type=Path)
    return parser


def create_app(settings: SlackBotSettings):
    try:
        from slack_bolt import App
    except ImportError as error:
        raise RuntimeError("Slack dependency group is required: uv sync --group slack") from error
    client, store = create_slack_dependencies(settings)
    app = App(token=settings.bot_token)
    handler = SlackMessageHandler(
        lambda user_id: ChatService(client, user_id),
        store,
        bot_user_id=settings.bot_user_id,
    )

    @app.event("message")
    def handle_message(body, event, ack, client):
        handler.handle(event, ack, client, team_id=body.get("team_id", settings.team_id))

    return app, handler


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = SlackBotSettings.from_environment(args.release_config)
    app, _handler = create_app(settings)
    try:
        from slack_bolt.adapter.socket_mode import SocketModeHandler
    except ImportError as error:
        raise RuntimeError("Slack dependency group is required: uv sync --group slack") from error
    SocketModeHandler(app, settings.app_token).start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Slack Bot の秘密値を release config と分離した設定。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from example.chat.service import create_control_client_from_release_config
from example.slackbot_frontend.store import FirestoreSlackThreadSessionStore


@dataclass(frozen=True)
class SlackBotSettings:
    release_config: Path
    app_token: str
    bot_token: str
    bot_user_id: str
    team_id: str

    @classmethod
    def from_environment(cls, release_config: Path) -> SlackBotSettings:
        values = {
            "app_token": os.environ.get("SLACK_APP_TOKEN", "").strip(),
            "bot_token": os.environ.get("SLACK_BOT_TOKEN", "").strip(),
            "bot_user_id": os.environ.get("SLACK_BOT_USER_ID", "").strip(),
            "team_id": os.environ.get("SLACK_TEAM_ID", "").strip(),
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError(f"missing Slack environment variables: {', '.join(missing)}")
        if not values["app_token"].startswith("xapp-"):
            raise ValueError("SLACK_APP_TOKEN must be a Socket Mode app token")
        if not values["bot_token"].startswith("xoxb-"):
            raise ValueError("SLACK_BOT_TOKEN must be a bot token")
        return cls(release_config=release_config, **values)


def create_slack_dependencies(settings: SlackBotSettings):
    release_client = create_control_client_from_release_config(settings.release_config)
    from cas_hosting_adapter.release_config import load_release_config

    release = load_release_config(settings.release_config)
    from google.cloud.firestore import Client

    firestore = Client(project=release.project_id, database=release.firestore_database)
    return release_client, FirestoreSlackThreadSessionStore(firestore)

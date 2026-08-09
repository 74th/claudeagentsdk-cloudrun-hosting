"""Slack thread と会話 session の永続対応ポートと Firestore 実装。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class SlackThreadKey:
    team_id: str
    channel_id: str
    thread_ts: str

    def __post_init__(self) -> None:
        if not all(
            value.strip() for value in (self.team_id, self.channel_id, self.thread_ts)
        ):
            raise ValueError("Slack thread key must not be blank")

    @property
    def document_id(self) -> str:
        material = "\0".join((self.team_id, self.channel_id, self.thread_ts))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SlackSessionBinding:
    key: SlackThreadKey
    application_user_id: str
    session_id: str


class SlackThreadSessionStore(Protocol):
    def get(self, key: SlackThreadKey) -> SlackSessionBinding | None: ...

    def create_if_absent(
        self, key: SlackThreadKey, *, application_user_id: str, session_id: str
    ) -> SlackSessionBinding: ...


def application_user_id(team_id: str, slack_user_id: str) -> str:
    """workspace と Slack user の組から安定した内部 user ID を作る。"""
    team_id = team_id.strip()
    slack_user_id = slack_user_id.strip()
    if not team_id or not slack_user_id:
        raise ValueError("team_id and slack_user_id must not be blank")
    digest = hashlib.sha256(f"slack-user\0{team_id}\0{slack_user_id}".encode()).hexdigest()
    return f"slack:{digest}"


class InMemorySlackThreadSessionStore:
    """Firestore ポートを検証する再起動可能なテストダブル。"""

    def __init__(self) -> None:
        self._values: dict[str, SlackSessionBinding] = {}

    def get(self, key: SlackThreadKey) -> SlackSessionBinding | None:
        return self._values.get(key.document_id)

    def create_if_absent(
        self, key: SlackThreadKey, *, application_user_id: str, session_id: str
    ) -> SlackSessionBinding:
        existing = self._values.get(key.document_id)
        if existing is not None:
            return existing
        binding = SlackSessionBinding(key, application_user_id, session_id)
        self._values[key.document_id] = binding
        return binding


class FirestoreSlackThreadSessionStore:
    """release config の named Firestore database に対応を保存する。"""

    def __init__(self, firestore: Any, *, collection: str = "slack_thread_sessions") -> None:
        self._firestore = firestore
        self._collection = collection

    def _reference(self, key: SlackThreadKey) -> Any:
        return self._firestore.collection(self._collection).document(key.document_id)

    @staticmethod
    def _decode(key: SlackThreadKey, data: dict[str, Any]) -> SlackSessionBinding:
        return SlackSessionBinding(
            key=key,
            application_user_id=str(data["application_user_id"]),
            session_id=str(data["session_id"]),
        )

    def get(self, key: SlackThreadKey) -> SlackSessionBinding | None:
        snapshot = self._reference(key).get()
        if not snapshot.exists:
            return None
        return self._decode(key, dict(snapshot.to_dict() or {}))

    def create_if_absent(
        self, key: SlackThreadKey, *, application_user_id: str, session_id: str
    ) -> SlackSessionBinding:
        reference = self._reference(key)
        transaction = self._firestore.transaction()

        def create(transaction: Any) -> SlackSessionBinding:
            snapshot = reference.get(transaction=transaction)
            if snapshot.exists:
                return self._decode(key, dict(snapshot.to_dict() or {}))
            binding = SlackSessionBinding(key, application_user_id, session_id)
            transaction.create(
                reference,
                {
                    "team_id": key.team_id,
                    "channel_id": key.channel_id,
                    "thread_ts": key.thread_ts,
                    "application_user_id": binding.application_user_id,
                    "session_id": binding.session_id,
                },
            )
            return binding

        try:
            from google.cloud.firestore import transactional
        except ImportError:
            # The provider import remains optional for unit tests using a fake.
            return create(transaction)
        return transactional(create)(transaction)

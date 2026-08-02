"""Minimal Streamlit control UI; provider access stays behind ControlClient."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from time import sleep
from typing import Protocol
from uuid import UUID, uuid4

from cas_hosting_adapter.control_client import ControlClient
from cas_hosting_adapter.factory import GoogleCloudSettings, create_google_cloud_control_client
from cas_hosting_adapter.models import ChatEvent, Run, Session, SessionPage
from cas_hosting_adapter.release_config import load_release_config


class IdentityProvider(Protocol):
    def user_id(self) -> str: ...


class ManualIdentity:
    def __init__(self, value: str) -> None:
        self._value = value

    def user_id(self) -> str:
        return self._value.strip()


class ChatViewModel:
    """Streamlit-independent UI actions, usable with in-memory test ports."""

    def __init__(self, client: ControlClient, identity: IdentityProvider) -> None:
        self._client = client
        self._identity = identity

    def sessions(self, cursor: str | None = None) -> SessionPage:
        return self._client.list_sessions(self._identity.user_id(), cursor=cursor, limit=20)

    def create_session(self, title: str = "") -> Session:
        return self._client.create_session(self._identity.user_id(), title=title)

    def session(self, session_id: str) -> Session:
        return self._client.get_session(self._identity.user_id(), session_id)

    def start(self, session: Session, message: str, idempotency_key: str) -> Run:
        return self._client.reserve_and_start(
            Run(
                user_id=self._identity.user_id(),
                session_id=session.id,
                workspace_id=session.workspace_id,
                idempotency_key=idempotency_key,
            ),
            message,
        )

    def subscribe(self, run_id: UUID, callback: Callable[[ChatEvent], None]) -> Callable[[], None]:
        return self._client.subscribe_from_cursor(run_id, None, callback)

    def events(self, run_id: UUID) -> list[ChatEvent]:
        return self._client.list_events(run_id)

    def cancel(self, run_id: UUID) -> Run:
        return self._client.cancel(run_id)


def create_control_client_from_release_config(path: Path) -> ControlClient:
    """Connect the sample UI to the Firestore and Cloud Run resources in a release file."""
    release = load_release_config(path)
    settings = GoogleCloudSettings(
        project=release.project_id,
        region=release.region,
        firestore_database="(default)",
        bucket_name=release.bucket_name,
        job_name=release.job_name,
    )
    return create_google_cloud_control_client(settings)


def create_view_from_release_config(path: Path, identity: IdentityProvider) -> ChatViewModel:
    return ChatViewModel(create_control_client_from_release_config(path), identity)


def render(identity: IdentityProvider, view: ChatViewModel | None = None) -> None:
    import streamlit as st

    st.title("Claude Job Chat")
    st.caption(f"user: {identity.user_id()}")
    if view is None:
        st.info("ControlClient を構成すると session / run / event / cancel を利用できます。")
        return
    if st.button("New session"):
        st.session_state["session"] = view.create_session()
    page = view.sessions()
    if not page.sessions:
        st.info("New session を選択して会話を開始してください。")
        return
    selected = st.selectbox(
        "Sessions", page.sessions, format_func=lambda item: item.title or item.id
    )
    run_id = selected.active_run_id or st.session_state.get(f"last-run:{selected.id}")
    if run_id is not None:
        st.caption(f"Run ID: `{run_id}`")
    message = st.chat_input("Message")
    if message and selected.active_run_id is None:
        run = view.start(selected, message, idempotency_key=str(uuid4()))
        st.session_state[f"last-run:{selected.id}"] = run.id
        run_id = run.id
        st.success(f"Started run: `{run.id}`")
    current_session = view.session(selected.id)
    if run_id is not None:
        if st.button("今すぐ更新"):
            st.rerun()
        for event in view.events(run_id):
            if event.type == "user":
                with st.chat_message("user"):
                    st.write(event.payload.get("content", ""))
            elif event.type in {"agent", "final"}:
                with st.chat_message("assistant"):
                    st.write(event.payload.get("content") or event.payload.get("output") or "")
            elif event.type == "tool_started":
                name = event.payload.get("name") or "ツール"
                with st.status(f"実行中: {name}"):
                    st.json(event.payload.get("input", {}))
            elif event.type == "tool_completed":
                st.caption("ツール完了")
            elif event.type == "progress" and event.payload.get("description"):
                st.caption(f"進捗: {event.payload['description']}")
        if current_session.active_run_id == run_id:
            st.info("Cloud Run Job が実行中です。イベントを自動更新しています。")
            sleep(2)
            st.rerun()
    if current_session.active_run_id and st.button("Cancel"):
        state = view.cancel(current_session.active_run_id).state.value
        st.warning("cancel requested" if state == "cancel_requested" else state)


if __name__ == "__main__":
    import streamlit as st

    release_path = st.sidebar.text_input("Release config", "release.example.yaml")
    identity = ManualIdentity(st.sidebar.text_input("User ID", "test-user"))

    @st.cache_resource
    def create_control_client(path: str) -> ControlClient:
        return create_control_client_from_release_config(Path(path))

    try:
        render(identity, ChatViewModel(create_control_client(release_path), identity))
    except Exception as error:
        st.error(f"ControlClient の初期化に失敗しました: {error}")

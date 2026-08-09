"""Minimal Streamlit control UI; provider access stays behind ControlClient."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from time import sleep
from typing import Protocol
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from cas_hosting_adapter.control_client import ControlClient
from cas_hosting_adapter.errors import ExecutionTemporaryError
from cas_hosting_adapter.factory import GoogleCloudSettings, create_google_cloud_control_client
from cas_hosting_adapter.models import (
    ChatEvent,
    InitialSessionResult,
    Run,
    Session,
    SessionPage,
)
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
        page = self._client.list_sessions(self._identity.user_id(), cursor=cursor, limit=20)
        return page.model_copy(
            update={
                "sessions": order_sessions(page.sessions),
            }
        )

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

    def start_new_session(self, message: str, idempotency_key: str) -> InitialSessionResult:
        return self._client.start_session(self._identity.user_id(), message, idempotency_key)

    def start_draft(self, message: str, idempotency_key: str) -> InitialSessionResult:
        return self.start_new_session(message, idempotency_key)

    def runs(self, session_id: str, *, page_size: int = 20) -> list[Run]:
        runs: list[Run] = []
        cursor: str | None = None
        while True:
            page = self._client.list_runs(
                self._identity.user_id(), session_id, cursor=cursor, limit=page_size
            )
            runs.extend(page.runs)
            if page.next_cursor is None:
                return runs
            cursor = page.next_cursor

    def events_for_session(self, session_id: str) -> list[ChatEvent]:
        events: list[ChatEvent] = []
        for run in self.runs(session_id):
            events.extend(self._normalise_legacy_events(self.events(run.id)))
        return events

    @staticmethod
    def _normalise_legacy_events(events: list[ChatEvent]) -> list[ChatEvent]:
        """Render old SDK tool-result events that were incorrectly stored as user events."""
        normalised: list[ChatEvent] = []
        for event in events:
            if event.type != "user" or not isinstance(event.payload.get("content"), list):
                normalised.append(event)
                continue
            blocks = event.payload["content"]
            converted: list[ChatEvent] = []
            for index, block in enumerate(blocks):
                if not isinstance(block, dict):
                    continue
                if "tool_use_id" in block:
                    converted.append(
                        event.model_copy(
                            update={
                                "id": f"{event.id}:tool-result:{index}",
                                "type": "tool_completed",
                                "payload": {
                                    "tool_id": block.get("tool_use_id", ""),
                                    "content": block.get("content"),
                                    "is_error": bool(block.get("is_error", False)),
                                },
                            }
                        )
                    )
                elif "name" in block and "input" in block:
                    converted.append(
                        event.model_copy(
                            update={
                                "id": f"{event.id}:tool-use:{index}",
                                "type": "tool_started",
                                "payload": {
                                    "tool_id": block.get("id", ""),
                                    "name": block.get("name", "ツール"),
                                    "input": block.get("input", {}),
                                },
                            }
                        )
                    )
            normalised.extend(converted or [event])
        return normalised

    def history(self, session_id: str) -> list[ChatEvent]:
        """Return displayable history without rendering a terminal answer twice.

        Claude Agent SDK emits the assistant text while it is streaming and
        emits the complete result again as a final event.  Keep the streaming
        event for active runs, but once a matching final event exists prefer
        that canonical terminal result.  The filtering is scoped to each run
        so identical answers in separate runs remain visible.
        """
        events = self.events_for_session(session_id)
        final_outputs_by_run: dict[UUID, set[str]] = {}
        for event in events:
            if event.type != "final":
                continue
            output = event.payload.get("output")
            if isinstance(output, str):
                final_outputs_by_run.setdefault(event.run_id, set()).add(output.strip())

        displayed: list[ChatEvent] = []
        seen_final_outputs: set[tuple[UUID, str]] = set()
        for event in events:
            if event.type == "agent":
                content = event.payload.get("content")
                if isinstance(content, str) and content.strip() in final_outputs_by_run.get(
                    event.run_id, set()
                ):
                    continue
            elif event.type == "final":
                output = event.payload.get("output")
                if isinstance(output, str):
                    key = (event.run_id, output.strip())
                    if key in seen_final_outputs:
                        continue
                    seen_final_outputs.add(key)
            displayed.append(event)
        return displayed

    def subscribe(self, run_id: UUID, callback: Callable[[ChatEvent], None]) -> Callable[[], None]:
        return self._client.subscribe_from_cursor(run_id, None, callback)

    def events(self, run_id: UUID) -> list[ChatEvent]:
        return self._client.list_events(run_id)

    def cancel(self, run_id: UUID) -> Run:
        return self._client.cancel(run_id)

    def reconcile(self, run_id: UUID) -> Run:
        """Refresh one active run without exposing any provider SDK to the UI."""
        return self._client.reconcile(run_id, holder="streamlit")


SELECTED_SESSION_KEY = "selected-session-id"
DRAFT_STATE_KEY = "session-draft"
DRAFT_IDEMPOTENCY_KEY = "draft-idempotency-key"
JST = ZoneInfo("Asia/Tokyo")


def order_sessions(sessions: list[Session]) -> list[Session]:
    """Keep the sidebar order newest-first, independent of provider ordering."""
    return sorted(sessions, key=lambda session: (session.updated_at, session.id), reverse=True)


def session_label(session: Session) -> str:
    """Render a session item with its last-update time in Japan Standard Time."""
    updated = session.updated_at.astimezone(JST)
    title = session.title.strip() or "Untitled session"
    return f"{updated:%Y-%m-%d %H:%M:%S JST} · {title}"


def create_control_client_from_release_config(path: Path) -> ControlClient:
    """Connect the sample UI to the Firestore and Cloud Run resources in a release file."""
    release = load_release_config(path)
    settings = GoogleCloudSettings(
        project=release.project_id,
        region=release.region,
        firestore_database=release.firestore_database,
        bucket_name=release.bucket_name,
        job_name=release.job_name,
        run_retention_days=release.run_retention_days,
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
    if st.sidebar.button("New session", key="new-session"):
        st.session_state.pop(SELECTED_SESSION_KEY, None)
        st.session_state[DRAFT_STATE_KEY] = True
        st.session_state[DRAFT_IDEMPOTENCY_KEY] = str(uuid4())
        st.rerun()
    page = view.sessions()
    sessions_by_id = {session.id: session for session in page.sessions}
    draft = bool(st.session_state.get(DRAFT_STATE_KEY, False))
    selected_id = st.session_state.get(SELECTED_SESSION_KEY)
    if selected_id not in sessions_by_id:
        selected_id = None
        st.session_state.pop(SELECTED_SESSION_KEY, None)

    st.sidebar.subheader("Sessions")
    if not page.sessions:
        st.sidebar.caption("保存済みセッションはありません。")
    for session in page.sessions:
        if session.id == selected_id:
            st.sidebar.info(session_label(session))
            continue
        if st.sidebar.button(
            session_label(session),
            key=f"select-session:{session.id}",
            type="secondary",
            use_container_width=True,
        ):
            st.session_state[SELECTED_SESSION_KEY] = session.id
            st.session_state[DRAFT_STATE_KEY] = False
            st.rerun()

    if selected_id is None and not draft:
        st.info("サイドバーから New session または既存セッションを選択してください。")
    message = st.chat_input("Message")
    if draft:
        st.subheader("New session")
        st.caption("未送信の draft には Session ID はありません。")
        if message:
            key = st.session_state.setdefault(DRAFT_IDEMPOTENCY_KEY, str(uuid4()))
            result = view.start_new_session(message, key)
            st.session_state[SELECTED_SESSION_KEY] = result.session.id
            st.session_state[f"last-run:{result.session.id}"] = result.run.id
            st.session_state[DRAFT_STATE_KEY] = False
            st.rerun()
        return

    if selected_id is None:
        return
    selected = view.session(selected_id)
    if selected.active_run_id is not None:
        try:
            view.reconcile(selected.active_run_id)
        except ExecutionTemporaryError:
            # Keep the last durable state visible and retry on the next refresh.
            pass
        selected = view.session(selected_id)
    runs = view.runs(selected.id)
    current_run = next((run for run in runs if run.id == selected.active_run_id), None)
    if current_run is None and runs:
        current_run = runs[-1]
    st.header(selected.title.strip() or "Untitled session")
    id_left, id_right = st.columns(2)
    id_left.caption(f"Session ID: `{selected.id}`")
    execution_name = (
        current_run.execution.name if current_run and current_run.execution else "pending"
    )
    id_right.caption(f"Cloud Run execution ID: `{execution_name}`")
    if current_run is not None:
        st.caption(f"Run ID: `{current_run.id}`")
        if current_run.state.terminal and current_run.error_code:
            st.error(f"実行は {current_run.state.value} で終了しました: {current_run.error_code}")
    if message and selected.active_run_id is None:
        result = view.start(selected, message, idempotency_key=str(uuid4()))
        st.session_state[f"last-run:{selected.id}"] = result.id
        st.rerun()
    for event in view.history(selected.id):
        if event.type == "user":
            with st.chat_message("user"):
                st.write(event.payload.get("content", ""))
        elif event.type in {"agent", "final"}:
            with st.chat_message("assistant"):
                st.write(event.payload.get("content") or event.payload.get("output") or "")
        elif event.type == "tool_started":
            name = event.payload.get("name") or "ツール"
            with st.status(f"実行中: {name}", expanded=False):
                st.json(event.payload.get("input", {}))
        elif event.type == "tool_completed":
            with st.status("ツール完了", state="complete", expanded=False):
                st.json(event.payload)
        elif event.type == "progress" and event.payload.get("description"):
            st.caption(f"進捗: {event.payload['description']}")
    if current_run is not None and selected.active_run_id == current_run.id:
        if st.button("今すぐ更新"):
            st.rerun()
        st.info("Cloud Run Job が実行中です。イベントを自動更新しています。")
        sleep(2)
        st.rerun()
    if selected.active_run_id and st.button("Cancel"):
        state = view.cancel(selected.active_run_id).state.value
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

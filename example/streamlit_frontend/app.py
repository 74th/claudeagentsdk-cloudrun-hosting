"""共通チャットサービスを表示へ変換する Streamlit UI。"""

from __future__ import annotations

from pathlib import Path
from time import sleep
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from cas_hosting_adapter.errors import ExecutionTemporaryError
from cas_hosting_adapter.models import ChatEvent, InitialSessionResult, Run, Session, SessionPage
from example.chat import (
    ChatService,
    CommonChatEvent,
    StaticIdentity,
    create_control_client_from_release_config,
)
from example.chat.events import normalize_events

ManualIdentity = StaticIdentity


class ChatViewModel:
    """Streamlit の state と共通サービスの間の薄い表示アダプター。"""

    def __init__(self, service: ChatService) -> None:
        self._service = service

    def sessions(self, cursor: str | None = None) -> SessionPage:
        page = self._service.list_sessions(cursor=cursor, limit=20)
        return page.model_copy(update={"sessions": order_sessions(page.sessions)})

    def create_session(self, title: str = "") -> Session:
        return self._service.create_session(title=title)

    def session(self, session_id: str) -> Session:
        return self._service.get_session(session_id)

    def start(self, session: Session, message: str, idempotency_key: str) -> Run:
        return self._service.start(
            message, session_id=session.id, idempotency_key=idempotency_key
        ).run

    def start_new_session(self, message: str, idempotency_key: str) -> InitialSessionResult:
        result = self._service.start(message, idempotency_key=idempotency_key)
        return InitialSessionResult(session=self.session(result.session_id), run=result.run)

    start_draft = start_new_session

    def runs(self, session_id: str, *, page_size: int = 20) -> list[Run]:
        runs: list[Run] = []
        cursor: str | None = None
        while True:
            page = self._service.list_runs(session_id, cursor=cursor, limit=page_size)
            runs.extend(page.runs)
            if page.next_cursor is None:
                return runs
            cursor = page.next_cursor

    def events_for_session(self, session_id: str) -> list[CommonChatEvent]:
        events: list[ChatEvent] = []
        for run in self.runs(session_id):
            events.extend(self._service.events(run.id))
        return normalize_events(events)

    def history(self, session_id: str) -> list[CommonChatEvent]:
        events = self.events_for_session(session_id)
        final_outputs_by_run: dict[UUID, set[str]] = {}
        for event in events:
            if event.type == "final" and isinstance(event.payload.get("output"), str):
                final_outputs_by_run.setdefault(event.run_id, set()).add(
                    event.payload["output"].strip()
                )
        displayed: list[CommonChatEvent] = []
        seen_final_outputs: set[tuple[UUID, str]] = set()
        for event in events:
            if event.type == "agent" and isinstance(event.payload.get("content"), str):
                if event.payload["content"].strip() in final_outputs_by_run.get(
                    event.run_id, set()
                ):
                    continue
            if event.type == "final" and isinstance(event.payload.get("output"), str):
                key = (event.run_id, event.payload["output"].strip())
                if key in seen_final_outputs:
                    continue
                seen_final_outputs.add(key)
            displayed.append(event)
        return displayed

    def stream(self, run: Run):
        return self._service.stream(run)

    def events(self, run_id: UUID) -> list[ChatEvent]:
        return self._service.events(run_id)

    def cancel(self, run_id: UUID) -> Run:
        return self._service.cancel(run_id)

    def reconcile(self, run_id: UUID) -> Run:
        return self._service.reconcile(run_id, holder="streamlit")


SELECTED_SESSION_KEY = "selected-session-id"
DRAFT_STATE_KEY = "session-draft"
DRAFT_IDEMPOTENCY_KEY = "draft-idempotency-key"
JST = ZoneInfo("Asia/Tokyo")


def order_sessions(sessions: list[Session]) -> list[Session]:
    return sorted(sessions, key=lambda session: (session.updated_at, session.id), reverse=True)


def session_label(session: Session) -> str:
    updated = session.updated_at.astimezone(JST)
    title = session.title.strip() or "Untitled session"
    return f"{updated:%Y-%m-%d %H:%M:%S JST} · {title}"


def create_view_from_release_config(path: Path, identity: StaticIdentity) -> ChatViewModel:
    return ChatViewModel(ChatService(create_control_client_from_release_config(path), identity))


def render(view: ChatViewModel | None = None) -> None:
    import streamlit as st

    st.title("Claude Job Chat")
    if view is None:
        st.info("共通チャットサービスを構成すると会話を利用できます。")
        return
    st.caption(f"user: {view._service.user_id}")
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
        if st.sidebar.button(session_label(session), key=f"select-session:{session.id}"):
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
            pass
        selected = view.session(selected_id)
    runs = view.runs(selected.id)
    current_run = next((run for run in runs if run.id == selected.active_run_id), None)
    if current_run is None and runs:
        current_run = runs[-1]
    st.header(selected.title.strip() or "Untitled session")
    st.caption(f"Session ID: `{selected.id}`")
    if current_run is not None:
        st.caption(f"Run ID: `{current_run.id}`")
        if current_run.state.terminal and current_run.error_code:
            st.error(f"実行は {current_run.state.value} で終了しました: {current_run.error_code}")
    if message and selected.active_run_id is None:
        view.start(selected, message, idempotency_key=str(uuid4()))
        st.rerun()
    for event in view.history(selected.id):
        if event.type == "user":
            with st.chat_message("user"):
                st.write(event.payload.get("content", ""))
        elif event.type in {"agent", "final"}:
            with st.chat_message("assistant"):
                st.write(event.content or "")
        elif event.type == "tool_started":
            with st.status(f"実行中: {event.payload.get('name') or 'ツール'}", expanded=False):
                st.json(event.payload.get("input", {}))
        elif event.type == "tool_completed":
            with st.status("ツール完了", state="complete", expanded=False):
                st.json(event.payload)
        elif event.type == "progress" and event.payload.get("description"):
            st.caption(f"進捗: {event.payload['description']}")
        elif event.type == "unknown":
            st.caption(f"未対応イベント: {event.raw_type}")
    if current_run is not None and selected.active_run_id == current_run.id:
        if st.button("今すぐ更新"):
            st.rerun()
        st.info("Cloud Run Job が実行中です。イベントを自動更新しています。")
        sleep(2)
        st.rerun()
    if selected.active_run_id and st.button("Cancel"):
        st.warning(view.cancel(selected.active_run_id).state.value)


if __name__ == "__main__":
    import streamlit as st

    release_path = st.sidebar.text_input("Release config", "release.example.yaml")
    identity = ManualIdentity(st.sidebar.text_input("User ID", "test-user"))
    try:
        render(create_view_from_release_config(Path(release_path), identity))
    except Exception:
        st.error("ControlClient の初期化に失敗しました。設定と認証を確認してください。")

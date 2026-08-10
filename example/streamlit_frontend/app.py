"""共通チャットサービスを表示へ変換する Streamlit UI。"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from cas_hosting_adapter.models import (
    ChatEvent,
    InitialSessionResult,
    QuestionRequest,
    Run,
    RunState,
    Session,
    SessionPage,
)
from example.chat import (
    ChatService,
    CommonChatEvent,
    StaticIdentity,
    create_control_client_from_release_config,
)
from example.chat.events import InteractionState, normalize_events

ManualIdentity = StaticIdentity
LOGGER = logging.getLogger(__name__)


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

    def run(self, session_id: str, run_id: UUID) -> Run:
        return self._service.get_run(session_id, run_id)

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

    def stream(self, run: Run) -> Iterator[CommonChatEvent]:
        return self._service.stream(run)

    def events(self, run_id: UUID) -> list[ChatEvent]:
        return self._service.events(run_id)

    def latest_event(self, run_id: UUID) -> ChatEvent | None:
        return self._service.latest_event(run_id)

    def interaction_state(self, session_id: str, run_id: UUID) -> InteractionState:
        return self._service.interaction_state_for_run(session_id, run_id)

    def pending_questions(self, session_id: str, run_id: UUID) -> list[QuestionRequest]:
        return self._service.pending_questions(session_id, run_id)

    def answer_question(
        self,
        session_id: str,
        run_id: UUID,
        question_id: str,
        answers: str | list[str],
        idempotency_key: str,
    ) -> QuestionRequest:
        return self._service.answer_question(
            session_id,
            run_id,
            question_id,
            answers,
            idempotency_key=idempotency_key,
        )

    def continue_after_question(
        self,
        session_id: str,
        question: QuestionRequest,
        answers: str | list[str],
        idempotency_key: str,
    ) -> Run:
        return self._service.continue_after_question(
            session_id,
            question,
            answers,
            idempotency_key=idempotency_key,
        ).run

    def cancel(self, run_id: UUID) -> Run:
        return self._service.cancel(run_id)

    def reconcile(self, run_id: UUID) -> Run:
        return self._service.reconcile(run_id, holder="streamlit")


SELECTED_SESSION_KEY = "selected-session-id"
DRAFT_STATE_KEY = "session-draft"
DRAFT_IDEMPOTENCY_KEY = "draft-idempotency-key"
APP_SYNC_RUN_KEY = "streamlit-app-sync-run-id"
FRAGMENT_AUTO_REFRESH_KEY = "streamlit-fragment-auto-refresh"
DYNAMIC_RENDER_STATE_KEY = "streamlit-dynamic-render-state"
DYNAMIC_OUTPUT_READY_KEY = "streamlit-dynamic-output-ready"
MONITORED_FRAGMENT_RUN_KEY = "streamlit-fragment-monitored-run"
JST = ZoneInfo("Asia/Tokyo")


def order_sessions(sessions: list[Session]) -> list[Session]:
    return sorted(sessions, key=lambda session: (session.updated_at, session.id), reverse=True)


def session_label(session: Session) -> str:
    updated = session.updated_at.astimezone(JST)
    title = session.title.strip() or "Untitled session"
    return f"{updated:%Y-%m-%d %H:%M:%S JST} · {title}"


def auto_refresh_allowed(interaction: InteractionState | None) -> bool:
    """Avoid rerunning Streamlit while a question widget is being answered."""
    return interaction is None or not interaction.pending_questions


def should_open_initial_draft(selected_id: str | None, draft_initialized: bool) -> bool:
    """Open the first page in the same state as the New session action."""
    return selected_id is None and not draft_initialized


class RefreshScope(StrEnum):
    """The smallest Streamlit scope that can represent the next UI update."""

    FRAGMENT = "fragment"
    APP = "app"
    NONE = "none"


@dataclass(frozen=True)
class RefreshDecision:
    """Pure result of reconciling one dynamic Run view."""

    scope: RefreshScope
    reason: str

    @property
    def should_refresh(self) -> bool:
        return self.scope is not RefreshScope.NONE


@dataclass(frozen=True)
class DynamicRevision:
    """Small durable marker used before loading the complete dynamic view."""

    session_updated_at: datetime
    active_run_id: UUID | None
    latest_run_state: str | None
    run_id: UUID | None
    run_state: RunState | None
    run_error_code: str | None
    run_finished_at: datetime | None
    latest_event: tuple[str, int] | None


@dataclass
class DynamicRenderState:
    """Cached values needed to replay the fragment without another full read."""

    session_id: str
    revision: DynamicRevision
    selected: Session
    current_run: Run | None
    history: list[CommonChatEvent]
    interaction: InteractionState | None
    finish_event_seen: bool


def dynamic_state_cache_hit(
    cached: object,
    session_id: str,
    revision: DynamicRevision,
) -> bool:
    """Return whether a cached dynamic model belongs to the current revision."""
    return (
        isinstance(cached, DynamicRenderState)
        and cached.session_id == session_id
        and cached.revision == revision
    )


def monitored_run_id_for_fragment(
    selected_id: str,
    active_run_id: UUID | None,
    monitored: tuple[str, UUID] | None,
) -> UUID | None:
    """Keep one Run ID long enough to observe its terminal transition."""
    if monitored is not None and monitored[0] == selected_id:
        return monitored[1]
    return active_run_id


def decide_refresh_scope(
    monitored_run_id: UUID | None,
    session: Session,
    current_run: Run | None,
    interaction: InteractionState | None,
    *,
    finish_event_seen: bool = False,
    temporary_error: bool = False,
    app_sync_already_requested: bool = False,
) -> RefreshDecision:
    """Choose fragment polling, no polling, or one app-wide synchronization.

    The function deliberately only consumes snapshots.  Reconciliation and
    rendering remain outside it so a temporary provider failure cannot be
    mistaken for a terminal transition.
    """
    if monitored_run_id is None:
        return RefreshDecision(RefreshScope.NONE, "no active run")
    if session.active_run_id != monitored_run_id:
        return RefreshDecision(RefreshScope.APP, "active run was cleared or replaced")
    if app_sync_already_requested:
        return RefreshDecision(RefreshScope.NONE, "app synchronization already requested")
    if finish_event_seen:
        return RefreshDecision(RefreshScope.APP, "finish event observed")
    if (
        current_run is not None
        and current_run.id == monitored_run_id
        and current_run.state.terminal
    ):
        return RefreshDecision(RefreshScope.APP, "run reached a terminal state")
    if interaction is not None and interaction.pending_questions:
        return RefreshDecision(RefreshScope.NONE, "a question is pending")
    if temporary_error:
        return RefreshDecision(RefreshScope.FRAGMENT, "temporary status error")
    return RefreshDecision(RefreshScope.FRAGMENT, "active run is still running")


def make_dynamic_revision(
    session: Session,
    current_run: Run | None,
    latest_event: ChatEvent | None,
) -> DynamicRevision:
    """Build a comparable marker without loading the complete conversation."""
    return DynamicRevision(
        session_updated_at=session.updated_at,
        active_run_id=session.active_run_id,
        latest_run_state=session.latest_run_state,
        run_id=current_run.id if current_run is not None else None,
        run_state=current_run.state if current_run is not None else None,
        run_error_code=current_run.error_code if current_run is not None else None,
        run_finished_at=current_run.finished_at if current_run is not None else None,
        latest_event=(latest_event.id, latest_event.sequence) if latest_event else None,
    )


def split_history(
    history: list[CommonChatEvent],
) -> tuple[list[CommonChatEvent], list[CommonChatEvent]]:
    """Separate stable user messages from the fragment-owned event stream."""
    return (
        [event for event in history if event.type == "user"],
        [event for event in history if event.type != "user"],
    )


def create_view_from_release_config(path: Path, identity: StaticIdentity) -> ChatViewModel:
    return ChatViewModel(ChatService(create_control_client_from_release_config(path), identity))


def request_fragment_rerun(st: Any) -> None:
    """Request a fragment rerun when the current execution supports its scope."""
    from streamlit.runtime.scriptrunner import get_script_run_ctx

    context = get_script_run_ctx(suppress_warning=True)
    if context is not None and context.fragment_ids_this_run:
        st.rerun(scope="fragment")
    else:
        # A widget event can be delivered as an app rerun by test/runtime
        # adapters.  The widget itself still belongs to the fragment.
        st.rerun()


def _is_finish_event(event: CommonChatEvent) -> bool:
    """Return whether an event is an explicit durable completion signal."""
    return event.type in {"finish", "finished", "terminal"}


def _render_history(
    st: Any,
    history: list[CommonChatEvent],
    *,
    include_user: bool = False,
    exclude_run_id: UUID | None = None,
) -> None:
    for event in history:
        if exclude_run_id is not None and event.run_id == exclude_run_id and event.type != "user":
            continue
        if event.type == "user":
            if not include_user:
                continue
            with st.chat_message("user"):
                st.write(event.payload.get("content", ""))
        elif event.type in {"agent", "final"}:
            with st.chat_message("assistant"):
                st.write(event.content or "")
                if event.type == "final" and event.processing_metadata.display_text:
                    st.caption(event.processing_metadata.display_text)
        elif event.type == "error":
            st.error(event.content or "処理に失敗しました。")
            if event.processing_metadata.display_text:
                st.caption(event.processing_metadata.display_text)
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


def _render_user_message(st: Any, message: str) -> None:
    with st.chat_message("user"):
        st.write(message)


def _load_interaction_state(
    view: ChatViewModel,
    selected: Session,
    question_run: Run | None,
) -> InteractionState | None:
    if question_run is None or not (
        selected.active_run_id == question_run.id or question_run.state is RunState.TIMED_OUT
    ):
        return None
    try:
        return view.interaction_state(selected.id, question_run.id)
    except Exception:
        return None


def _render_questions_and_tasks(
    st: Any,
    view: ChatViewModel,
    selected: Session,
    question_run: Run | None,
    interaction: InteractionState | None,
) -> InteractionState | None:
    if question_run is None or not (
        selected.active_run_id == question_run.id or question_run.state is RunState.TIMED_OUT
    ):
        return None
    if interaction is None:
        return None
    if interaction.pending_questions:
        st.subheader("回答待ちの質問")
        for question in interaction.pending_questions:
            st.markdown(f"**{question.header or '質問'}**")
            st.write(question.question)
            option_labels = [option.label for option in question.options]
            other_label = "その他"
            choices = option_labels + [other_label]
            widget_key = f"question-answer:{question.id}"
            selected_values = (
                st.multiselect(widget_key, choices, key=f"{widget_key}:choices")
                if question.multi_select
                else [st.radio(widget_key, choices, key=f"{widget_key}:choice")]
            )
            free_text = ""
            if other_label in selected_values:
                free_text = st.text_input(
                    "その他の回答", key=f"{widget_key}:other", disabled=False
                ).strip()
            values = [value for value in selected_values if value != other_label]
            if other_label in selected_values:
                values = [free_text] if free_text else []
            sent_key = f"{widget_key}:sent"
            if st.button(
                "回答を送信",
                key=f"{widget_key}:submit",
                disabled=bool(st.session_state.get(sent_key, False)),
            ):
                if not values:
                    st.error("選択または「その他」の入力が必要です。")
                else:
                    try:
                        st.session_state[sent_key] = True
                        if question_run.state is RunState.TIMED_OUT:
                            view.continue_after_question(
                                selected.id,
                                question,
                                values,
                                f"streamlit:continuation:{question_run.id}:{question.id}",
                            )
                            st.success("回答を反映して会話を再開しました。")
                        else:
                            view.answer_question(
                                selected.id,
                                question_run.id,
                                question.id,
                                values,
                                f"streamlit:{question.id}",
                            )
                            st.success("回答を受け付けました。")
                        st.session_state[FRAGMENT_AUTO_REFRESH_KEY] = True
                        st.rerun(scope="app")
                    except Exception:
                        st.session_state[sent_key] = False
                        st.error("回答を受け付けられませんでした。再読み込みして状態を確認してください。")
    if interaction.task_list:
        st.subheader("タスク進捗")
        for task in interaction.task_list:
            dependencies = (
                f"（依存: {', '.join(task.blocked_by)}）" if task.blocked_by else ""
            )
            st.write(f"`{task.task_id}` · {task.status} · {task.subject} {dependencies}")
    return interaction


def _render_dynamic_content(
    st: Any,
    view: ChatViewModel,
    selected: Session,
    current_run: Run | None,
    history: list[CommonChatEvent],
    interaction: InteractionState | None,
) -> None:
    _render_history(st, history)
    _render_questions_and_tasks(st, view, selected, current_run, interaction)


def render_dynamic_area(
    view: ChatViewModel,
    selected_id: str,
    *,
    auto_refresh_scheduled: bool = False,
    output_container: Any | None = None,
) -> None:
    """Render and poll the selected session inside a Streamlit fragment."""
    import streamlit as st

    selected = view.session(selected_id)
    monitored = st.session_state.get(MONITORED_FRAGMENT_RUN_KEY)
    monitored_run_id = monitored_run_id_for_fragment(
        selected_id,
        selected.active_run_id,
        monitored if isinstance(monitored, tuple) and len(monitored) == 2 else None,
    )
    if monitored_run_id is None:
        st.session_state.pop(APP_SYNC_RUN_KEY, None)
    sync_run_key = str(monitored_run_id) if monitored_run_id is not None else None
    app_sync_already_requested = (
        sync_run_key is not None and st.session_state.get(APP_SYNC_RUN_KEY) == sync_run_key
    )
    cached = st.session_state.get(DYNAMIC_RENDER_STATE_KEY)
    cached_for_session = (
        cached
        if isinstance(cached, DynamicRenderState)
        and cached.session_id == selected_id
        and monitored_run_id is not None
        and cached.current_run is not None
        and cached.current_run.id == monitored_run_id
        else None
    )
    temporary_error = False

    # Run state and Session.active_run_id are persisted by the worker in
    # Firestore. A provider status request can be much slower than the UI
    # polling interval, so it must not gate rendering the durable events.
    current_run: Run | None = None
    if monitored_run_id is not None:
        try:
            current_run = view.run(selected.id, monitored_run_id)
        except Exception:
            temporary_error = True
            if cached_for_session is not None:
                current_run = cached_for_session.current_run
            LOGGER.warning("Streamlit dynamic Run read failed", exc_info=True)
    else:
        # A timed-out run can leave the session without an active_run_id while
        # its interaction still needs to be displayed for a final answer.
        try:
            runs = view.runs(selected.id)
            current_run = runs[-1] if runs else None
        except Exception:
            temporary_error = True
            LOGGER.warning("Streamlit latest Run read failed", exc_info=True)

    latest_event: ChatEvent | None = None
    if monitored_run_id is not None:
        try:
            latest_event = view.latest_event(monitored_run_id)
        except Exception:
            temporary_error = True
            LOGGER.warning("Streamlit dynamic latest event read failed", exc_info=True)

    revision = make_dynamic_revision(selected, current_run, latest_event)
    cache_hit = dynamic_state_cache_hit(cached, selected_id, revision)
    if temporary_error and cached_for_session is not None:
        # Keep the last complete dynamic view visible while a marker read is
        # unavailable. The next scheduled fragment run will retry it.
        cache_hit = True
        current_run = cached_for_session.current_run
        history = cached_for_session.history
        interaction = cached_for_session.interaction
        finish_event_seen = cached_for_session.finish_event_seen
    if cache_hit:
        # The fragment still wakes up on its schedule, but avoids the full
        # history and interaction reads when the durable revision is unchanged.
        current_run = cached.current_run
        history = cached.history
        interaction = cached.interaction
        finish_event_seen = cached.finish_event_seen
    else:
        if monitored_run_id is None:
            history = []
            interaction = None
            finish_event_seen = False
        else:
            try:
                _, history = split_history(view.history(selected.id))
            except Exception:
                temporary_error = True
                history = cached_for_session.history if cached_for_session is not None else []
                LOGGER.warning("Streamlit dynamic history read failed", exc_info=True)
            interaction = _load_interaction_state(view, selected, current_run)
            finish_event_seen = (
                not app_sync_already_requested
                and any(
                    event.run_id == monitored_run_id and _is_finish_event(event)
                    for event in history
                )
            )
        st.session_state[DYNAMIC_RENDER_STATE_KEY] = DynamicRenderState(
            session_id=selected_id,
            revision=revision,
            selected=selected,
            current_run=current_run,
            history=history,
            interaction=interaction,
            finish_event_seen=finish_event_seen,
        )

    decision = decide_refresh_scope(
        monitored_run_id,
        selected,
        current_run,
        interaction,
        finish_event_seen=finish_event_seen,
        temporary_error=temporary_error,
        app_sync_already_requested=app_sync_already_requested,
    )
    if decision.scope is RefreshScope.APP:
        if sync_run_key is not None:
            st.session_state[APP_SYNC_RUN_KEY] = sync_run_key
        if selected.active_run_id != monitored_run_id or (
            current_run is not None
            and current_run.id == monitored_run_id
            and current_run.state.terminal
        ):
            st.session_state[FRAGMENT_AUTO_REFRESH_KEY] = False
        st.rerun(scope="app")

    render_content = (
        not cache_hit
        or not st.session_state.get(DYNAMIC_OUTPUT_READY_KEY, False)
        or (interaction is not None and bool(interaction.pending_questions))
    )
    if render_content:
        if output_container is None:
            _render_dynamic_content(st, view, selected, current_run, history, interaction)
        else:
            output_container.empty()
            with output_container.container():
                _render_dynamic_content(st, view, selected, current_run, history, interaction)
        st.session_state[DYNAMIC_OUTPUT_READY_KEY] = True

    if selected.active_run_id and st.button("Cancel"):
        st.warning(view.cancel(selected.active_run_id).state.value)
        st.rerun(scope="app")

    if (
        decision.scope is RefreshScope.NONE
        and interaction is not None
        and interaction.pending_questions
    ):
        if auto_refresh_scheduled:
            st.session_state[FRAGMENT_AUTO_REFRESH_KEY] = False
            st.rerun(scope="app")
        st.info("回答待ちのため自動更新を停止しています。回答後に状態を更新します。")

    if decision.scope is RefreshScope.FRAGMENT:
        if st.button("今すぐ更新"):
            request_fragment_rerun(st)
        st.info(
            "状態確認に一時的なエラーがあるため再試行します。"
            if temporary_error
            else "Cloud Run Job が実行中です。イベントを自動更新しています。"
        )


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
    draft_initialized = DRAFT_STATE_KEY in st.session_state
    selected_id = st.session_state.get(SELECTED_SESSION_KEY)
    if selected_id not in sessions_by_id:
        selected_id = None
        st.session_state.pop(SELECTED_SESSION_KEY, None)
    if should_open_initial_draft(selected_id, draft_initialized):
        st.session_state[DRAFT_STATE_KEY] = True
    draft = bool(st.session_state.get(DRAFT_STATE_KEY, False))
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
            _render_user_message(st, message)
            key = st.session_state.setdefault(DRAFT_IDEMPOTENCY_KEY, str(uuid4()))
            result = view.start_new_session(message, key)
            st.session_state[SELECTED_SESSION_KEY] = result.session.id
            st.session_state[DRAFT_STATE_KEY] = False
            st.rerun()
        return
    if selected_id is None:
        return
    if message and view.session(selected_id).active_run_id is None:
        _render_user_message(st, message)
        view.start(view.session(selected_id), message, idempotency_key=str(uuid4()))
        st.rerun()

    selected_snapshot = view.session(selected_id)
    st.header(selected_snapshot.title.strip() or "Untitled session")
    st.caption(f"Session ID: `{selected_snapshot.id}`")
    static_history = view.history(selected_id)
    _render_history(
        st,
        static_history,
        include_user=True,
        exclude_run_id=selected_snapshot.active_run_id,
    )
    if selected_snapshot.active_run_id is None:
        st.session_state.pop(FRAGMENT_AUTO_REFRESH_KEY, None)
        st.session_state.pop(MONITORED_FRAGMENT_RUN_KEY, None)
        st.session_state.pop(APP_SYNC_RUN_KEY, None)
    else:
        st.session_state[MONITORED_FRAGMENT_RUN_KEY] = (
            selected_snapshot.id,
            selected_snapshot.active_run_id,
        )
    auto_refresh_scheduled = (
        selected_snapshot.active_run_id is not None
        and st.session_state.get(FRAGMENT_AUTO_REFRESH_KEY, True)
    )
    dynamic_output = st.empty()
    st.session_state[DYNAMIC_OUTPUT_READY_KEY] = False

    @st.fragment(run_every=2 if auto_refresh_scheduled else None)
    def render_selected_session() -> None:
        render_dynamic_area(
            view,
            selected_id,
            auto_refresh_scheduled=auto_refresh_scheduled,
            output_container=dynamic_output,
        )

    render_selected_session()


if __name__ == "__main__":
    import streamlit as st

    release_path = st.sidebar.text_input("Release config", "release.example.yaml")
    identity = ManualIdentity(st.sidebar.text_input("User ID", "test-user"))
    try:
        render(create_view_from_release_config(Path(release_path), identity))
    except Exception:
        st.error("ControlClient の初期化に失敗しました。設定と認証を確認してください。")

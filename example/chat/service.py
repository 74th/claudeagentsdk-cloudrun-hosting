"""会話の開始と保存イベントのストリーミングを提供するアプリケーション層。"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from time import sleep
from uuid import UUID, uuid4

from cas_hosting_adapter.control_client import ControlClient
from cas_hosting_adapter.errors import ExecutionTemporaryError
from cas_hosting_adapter.factory import GoogleCloudSettings, create_google_cloud_control_client
from cas_hosting_adapter.models import (
    ChatEvent,
    QuestionRequest,
    Run,
    RunPage,
    Session,
    SessionPage,
)
from cas_hosting_adapter.release_config import load_release_config

from .events import (
    ChatEventKind,
    CommonChatEvent,
    InteractionState,
    normalize_event,
    normalize_events,
)
from .events import (
    interaction_state as build_interaction_state,
)
from .identity import IdentityProvider

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatStartResult:
    """run 開始結果。フロントエンドは provider object を受け取らない。"""

    session_id: str
    run_id: UUID
    run: Run


class ChatService:
    """ControlClient を CLI、Streamlit、Slack から共通利用する境界。"""

    def __init__(self, client: ControlClient, identity: str | IdentityProvider) -> None:
        self._client = client
        self._identity = identity if isinstance(identity, str) else identity.user_id()
        self._identity = self._identity.strip()
        if not self._identity:
            raise ValueError("user_id must not be blank")

    @property
    def user_id(self) -> str:
        return self._identity

    def start(
        self,
        prompt: str,
        *,
        session_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> ChatStartResult:
        """新規または既存 session に冪等な run を開始する。"""
        if not prompt.strip():
            raise ValueError("prompt must not be blank")
        key = (idempotency_key or str(uuid4())).strip()
        if not key:
            raise ValueError("idempotency_key must not be blank")
        if session_id is None:
            result = self._client.start_session(self.user_id, prompt, key)
            return ChatStartResult(result.session.id, result.run.id, result.run)

        session = self._client.get_session(self.user_id, session_id)
        run = Run(
            user_id=self.user_id,
            session_id=session.id,
            workspace_id=session.workspace_id,
            idempotency_key=key,
        )
        started = self._client.reserve_and_start(run, prompt)
        return ChatStartResult(session.id, started.id, started)

    # Descriptive aliases make the boundary convenient for non-UI callers.
    start_run = start
    start_conversation = start

    def history(self, session_id: str) -> list[CommonChatEvent]:
        """セッション内全 run の履歴を順序どおりに返す。"""
        events: list[ChatEvent] = []
        runs: list[Run] = []
        cursor: str | None = None
        while True:
            page = self._client.list_runs(
                self.user_id, session_id, cursor=cursor, limit=100
            )
            runs.extend(page.runs)
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        for run in runs:
            events.extend(self._client.list_events(run.id))
        return normalize_events(events)

    def stream(
        self,
        run: Run | UUID,
        *,
        session_id: str | None = None,
        reconnect_delay: float = 0.05,
    ) -> Iterator[CommonChatEvent]:
        """保存済みイベントと新着イベントを一度だけ返す。

        ``ControlClient.subscribe_from_cursor`` が提供する catch-up と live
        subscription の境界を利用し、購読エラー時は最後の event ID から再接続する。
        """
        run_id, owned_session_id = self._run_reference(run, session_id)
        queue: Queue[ChatEvent] = Queue()
        seen: set[str] = set()
        last_cursor: str | None = None
        terminal_sent = False
        final_seen = False
        unsubscribe: Callable[[], None] | None = None
        delay = max(0.0, reconnect_delay)

        def receive(event: ChatEvent) -> None:
            if event.id not in seen:
                queue.put(event)

        try:
            while True:
                if unsubscribe is None:
                    try:
                        unsubscribe = self._client.subscribe_from_cursor(
                            run_id, last_cursor, receive
                        )
                    except (ExecutionTemporaryError, ConnectionError, TimeoutError) as error:
                        LOGGER.debug("chat stream subscription retry: %s", type(error).__name__)
                        if delay:
                            sleep(delay)
                        delay = min(max(delay * 2, 0.05), 1.0)
                        continue
                    delay = max(0.0, reconnect_delay)

                try:
                    event = queue.get(timeout=0.1)
                except Empty:
                    current = self._client.get_run(self.user_id, owned_session_id, run_id)
                    if current.state.terminal and not terminal_sent and not final_seen:
                        terminal_sent = True
                        yield self._terminal_event(current)
                    if current.state.terminal:
                        return
                    # Firestore watches can finish without raising. Re-register
                    # from the durable cursor so a later event is not missed.
                    if unsubscribe is not None:
                        unsubscribe()
                        unsubscribe = None
                    continue

                if event.id in seen:
                    continue
                seen.add(event.id)
                last_cursor = event.id
                normalized = normalize_event(event)
                yield normalized
                if normalized.kind is ChatEventKind.FINAL:
                    final_seen = True
                    current = self._client.get_run(self.user_id, owned_session_id, run_id)
                    if current.state.terminal:
                        return
                if normalized.kind is ChatEventKind.TERMINAL:
                    return
        finally:
            if unsubscribe is not None:
                unsubscribe()

    stream_events = stream

    def get_run(self, session_id: str, run_id: UUID) -> Run:
        """利用者境界を保った run 状態の取得。"""
        return self._client.get_run(self.user_id, session_id, run_id)

    def list_sessions(self, *, cursor: str | None = None, limit: int = 20) -> SessionPage:
        return self._client.list_sessions(self.user_id, cursor=cursor, limit=limit)

    def create_session(self, *, title: str = "") -> Session:
        return self._client.create_session(self.user_id, title=title)

    def get_session(self, session_id: str) -> Session:
        return self._client.get_session(self.user_id, session_id)

    def list_runs(
        self, session_id: str, *, cursor: str | None = None, limit: int = 20
    ) -> RunPage:
        return self._client.list_runs(self.user_id, session_id, cursor=cursor, limit=limit)

    def events(self, run_id: UUID) -> list[ChatEvent]:
        return self._client.list_events(run_id)

    def latest_event(self, run_id: UUID) -> ChatEvent | None:
        return self._client.latest_event(run_id)

    def pending_questions(self, session_id: str, run_id: UUID) -> list[QuestionRequest]:
        """Return only questions visible inside this user's active run."""
        return [
            question
            for question in self._client.list_questions(self.user_id, session_id, run_id)
            if question.pending
        ]

    def answer_question(
        self,
        session_id: str,
        run_id: UUID,
        question_id: str,
        answers: str | list[str],
        *,
        idempotency_key: str,
    ) -> QuestionRequest:
        return self._client.answer_question(
            self.user_id,
            session_id,
            run_id,
            question_id,
            answers,
            idempotency_key,
        )

    def continue_after_question(
        self,
        session_id: str,
        question: QuestionRequest,
        answers: str | list[str],
        *,
        idempotency_key: str,
    ) -> ChatStartResult:
        """Start a resumable continuation after a timed-out question.

        The original SDK callback belongs to the terminated Job, so a late
        answer is carried into a new run as an explicit user instruction.
        The new Job resumes the session snapshot stored on the parent session.
        """
        values = [answers] if isinstance(answers, str) else list(answers)
        if not values or any(not value.strip() for value in values):
            raise ValueError("question answers must not be blank")
        prompt = (
            "前回の実行は、利用者への質問待ちのままタイムアウトしました。"
            "前回の会話コンテキストを引き継ぎ、以下の回答を反映して作業を続行してください。\n\n"
            f"質問: {question.question}\n"
            f"利用者の回答: {json.dumps(values, ensure_ascii=False)}"
        )
        return self.start(
            prompt,
            session_id=session_id,
            idempotency_key=idempotency_key,
        )

    def interaction_state(
        self, events: list[ChatEvent] | list[CommonChatEvent]
    ) -> InteractionState:
        """Reduce a durable event list to the common interaction view model."""
        if not events:
            return build_interaction_state([])
        first = events[0]
        normalized = (
            events if isinstance(first, CommonChatEvent) else normalize_events(events)  # type: ignore[arg-type]
        )
        return build_interaction_state(normalized)

    def interaction_state_for_run(self, session_id: str, run_id: UUID) -> InteractionState:
        state = self.interaction_state(self.events(run_id))
        # The canonical question collection is authoritative if an event watch
        # was disconnected, while task state remains event-derived.
        pending = tuple(self.pending_questions(session_id, run_id))
        return InteractionState(
            pending_questions=pending,
            tasks=state.tasks,
            task_order=state.task_order,
        )

    def cancel(self, run_id: UUID) -> Run:
        return self._client.cancel(run_id)

    def reconcile(self, run_id: UUID, *, holder: str = "chat-client") -> Run:
        return self._client.reconcile(run_id, holder=holder)

    def _run_reference(self, run: Run | UUID, session_id: str | None) -> tuple[UUID, str]:
        if isinstance(run, Run):
            if run.user_id != self.user_id:
                raise ValueError("run does not belong to user")
            return run.id, run.session_id
        if session_id is None or not session_id.strip():
            raise ValueError("session_id is required when run is a UUID")
        self._client.get_run(self.user_id, session_id, run)
        return run, session_id

    @staticmethod
    def _terminal_event(run: Run) -> CommonChatEvent:
        event = ChatEvent(
            id=f"terminal:{run.id}:{run.state.value}",
            run_id=run.id,
            sequence=2**63 - 1,
            type="terminal",
            payload={
                "state": run.state.value,
                "error_code": run.error_code,
                "result": run.result,
            },
        )
        return normalize_event(event)


def create_control_client_from_release_config(path: Path) -> ControlClient:
    """release config から provider SDK を共通層の外側で構成する。"""
    release = load_release_config(path)
    settings = GoogleCloudSettings(
        project=release.project_id,
        region=release.region,
        firestore_database=release.firestore_database,
        bucket_name=release.bucket_name,
        job_name=release.job_name,
        retention_days=release.retention_days,
        execution_platform=release.execution_platform,
        image=release.image,
        batch_job_id_prefix=release.cloud_batch.job_id_prefix,
        batch_machine_type=release.cloud_batch.machine_type,
        batch_cpu_milli=release.cloud_batch.cpu_milli,
        batch_memory_mib=release.cloud_batch.memory_mib,
        gke_cluster=release.gke.cluster if release.gke is not None else "",
        gke_cluster_region=release.gke.cluster_region if release.gke is not None else "",
        gke_namespace=release.gke.namespace if release.gke is not None else "claude-agent",
        gke_ksa_name=release.gke.ksa_name if release.gke is not None else "claude-agent",
        gke_kube_context=release.gke.kube_context if release.gke is not None else "",
        gke_cpu=release.gke.cpu if release.gke is not None else "1",
        gke_memory=release.gke.memory if release.gke is not None else "2Gi",
        gke_job_ttl_seconds=release.gke.job_ttl_seconds if release.gke is not None else 3600,
        task_timeout_seconds=release.task_timeout_seconds,
        question_timeout_seconds=release.question_timeout_seconds,
        vertex_region=release.vertex_region,
        claude_model=release.claude_model,
        log_level=release.log_level,
    )
    return create_google_cloud_control_client(settings)

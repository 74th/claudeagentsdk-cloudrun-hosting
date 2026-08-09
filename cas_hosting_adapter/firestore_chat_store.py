"""Firestore adapter for the provider-neutral ChatStore contract."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from .errors import ActiveRunConflictError, SessionNotFoundError, SessionOwnershipError
from .firestore_codec import (
    DEFAULT_RETENTION_DAYS,
    decode_timestamp,
    encode_event,
    encode_run,
    encode_session,
    is_expired,
    user_key,
)
from .models import (
    ChatEvent,
    ExecutionReference,
    InitialSessionResult,
    ReconciliationLease,
    Run,
    RunPage,
    RunState,
    Session,
    SessionPage,
)
from .protocols import Clock


class FirestoreChatStore:
    def __init__(
        self,
        client: Any,
        *,
        collection: str = "users",
        clock: Clock | Callable[[], datetime] | None = None,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        self._client = client
        self._collection = collection
        self._clock = clock
        self._retention_days = retention_days

    def _now(self) -> datetime:
        if self._clock is None:
            return datetime.now(UTC)
        value = self._clock() if callable(self._clock) else self._clock.now()
        return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)

    def _sessions(self, user_id: str) -> Any:
        user = self._client.collection(self._collection).document(user_key(user_id))
        return user.collection("sessions")

    def create_session(self, user_id: str, *, title: str = "") -> Session:
        now = self._now()
        session = Session(
            id=str(uuid4()),
            user_id=user_id,
            workspace_id=str(uuid4()),
            title=title,
            created_at=now,
            updated_at=now,
        )
        self._sessions(user_id).document(session.id).create(
            encode_session(session, retention_days=self._retention_days, now=now)
        )
        return session

    def reserve_initial_run(
        self, session: Session, run: Run, event: ChatEvent
    ) -> InitialSessionResult:
        """Create session, first run, and first user event in one transaction."""
        from google.cloud.firestore import transactional

        if (
            session.user_id != run.user_id
            or session.id != run.session_id
            or session.workspace_id != run.workspace_id
            or event.run_id != run.id
        ):
            raise ValueError("initial session, run, and event references do not match")
        session_ref = self._sessions(session.user_id).document(session.id)
        run_ref = session_ref.collection("runs").document(str(run.id))
        event_ref = run_ref.collection("events").document(event.id)
        now = self._now()
        transaction = self._client.transaction()

        def reserve(transaction: Any) -> InitialSessionResult:
            existing_session_snapshot = session_ref.get(transaction=transaction)
            session_exists = existing_session_snapshot.exists
            if existing_session_snapshot.exists:
                raw_session = dict(existing_session_snapshot.to_dict())
                if is_expired(raw_session, now):
                    raise SessionNotFoundError("session has expired")
                existing_session = self._decode_session(raw_session)
                if existing_session.user_id != session.user_id:
                    raise SessionOwnershipError("session belongs to another user")
                existing_run_snapshot = run_ref.get(transaction=transaction)
                if existing_run_snapshot.exists:
                    return InitialSessionResult(
                        session=existing_session,
                        run=self._decode_run(existing_run_snapshot.to_dict()),
                    )
                if existing_session.active_run_id is not None:
                    raise ActiveRunConflictError(str(existing_session.active_run_id))

            run_payload = encode_run(run, retention_days=self._retention_days, now=now)
            run_payload["next_sequence"] = 1
            session_update = {
                "active_run_id": str(run.id),
                "latest_run_state": run.state.value,
                "updated_at": now,
                "expires_at": now + timedelta(days=self._retention_days),
            }
            if session_exists:
                transaction.update(session_ref, session_update)
            else:
                transaction.create(
                    session_ref,
                    encode_session(
                        session.model_copy(
                            update={
                                "active_run_id": run.id,
                                "latest_run_state": run.state.value,
                                "updated_at": now,
                            }
                        ),
                        retention_days=self._retention_days,
                        now=now,
                    ),
                )
            transaction.create(run_ref, run_payload)
            transaction.create(event_ref, encode_event(event, retention_days=self._retention_days))
            return InitialSessionResult(
                session=session.model_copy(
                    update={
                        "active_run_id": run.id,
                        "latest_run_state": run.state.value,
                        "updated_at": now,
                    }
                ),
                run=run,
            )

        return cast(InitialSessionResult, transactional(reserve)(transaction))

    def get_session(self, user_id: str, session_id: str) -> Session:
        snapshot = self._sessions(user_id).document(session_id).get()
        if not snapshot.exists:
            raise SessionNotFoundError("session was not found")
        payload = dict(snapshot.to_dict())
        if is_expired(payload, self._now()):
            raise SessionNotFoundError("session has expired")
        session = self._decode_session(payload)
        if session.user_id != user_id:
            raise SessionOwnershipError("session belongs to another user")
        return session

    def list_sessions(self, user_id: str, *, cursor: str | None, limit: int) -> SessionPage:
        if limit < 1:
            raise ValueError("limit must be positive")
        query = (
            self._sessions(user_id)
            .order_by("updated_at", direction="DESCENDING")
            .order_by("id", direction="DESCENDING")
        )
        if cursor is not None:
            updated_at, session_id = self._decode_cursor(cursor)
            query = query.start_after({"updated_at": updated_at, "id": session_id})
        # TTL deletion is asynchronous and legacy documents may be overfetched;
        # scan until enough visible documents are found instead of treating a
        # fixed ``limit + 1`` document window as the page.
        snapshots = list(query.stream())
        visible = [
            snapshot
            for snapshot in snapshots
            if not is_expired(dict(snapshot.to_dict()), self._now())
        ]
        page = [self._decode_session(snapshot.to_dict()) for snapshot in visible[:limit]]
        next_cursor = None
        if len(visible) > limit:
            last = page[-1]
            next_cursor = self._encode_cursor(last.updated_at, last.id)
        return SessionPage(sessions=page, next_cursor=next_cursor)

    def list_runs(
        self, user_id: str, session_id: str, *, cursor: str | None, limit: int
    ) -> RunPage:
        if limit < 1:
            raise ValueError("limit must be positive")
        session = self.get_session(user_id, session_id)
        query = self._sessions(user_id).document(session.id).collection("runs")
        query = query.order_by("created_at").order_by("id")
        if cursor is not None:
            created_at, run_id = self._decode_run_cursor(cursor)
            query = query.start_after({"created_at": created_at, "id": run_id})
        snapshots = list(query.stream())
        visible = [
            snapshot
            for snapshot in snapshots
            if not is_expired(dict(snapshot.to_dict()), self._now())
        ]
        page = [self._decode_run(snapshot.to_dict()) for snapshot in visible[:limit]]
        next_cursor = None
        if len(visible) > limit and page:
            last = page[-1]
            next_cursor = self._encode_run_cursor(last.created_at, str(last.id))
        return RunPage(runs=page, next_cursor=next_cursor)

    def list_session_runs(
        self, user_id: str, session_id: str, *, cursor: str | None, limit: int
    ) -> RunPage:
        return self.list_runs(user_id, session_id, cursor=cursor, limit=limit)

    def reserve_run(self, run: Run, event: ChatEvent) -> Run:
        from google.cloud.firestore import transactional

        session_ref = self._sessions(run.user_id).document(run.session_id)
        run_ref = session_ref.collection("runs").document(str(run.id))
        event_ref = run_ref.collection("events").document(event.id)
        transaction = self._client.transaction()

        def reserve(transaction: Any) -> Run:
            session_snapshot = session_ref.get(transaction=transaction)
            if not session_snapshot.exists:
                raise SessionNotFoundError("session was not found")
            session = self._decode_session(session_snapshot.to_dict())
            existing = list(
                session_ref.collection("runs")
                .where("idempotency_key", "==", run.idempotency_key)
                .limit(1)
                .stream(transaction=transaction)
            )
            if existing:
                return self._decode_run(existing[0].to_dict())
            if session.active_run_id is not None:
                raise ActiveRunConflictError(str(session.active_run_id))
            now = self._now()
            run_payload = encode_run(run, retention_days=self._retention_days, now=now)
            run_payload["next_sequence"] = 1
            transaction.create(run_ref, run_payload)
            transaction.create(event_ref, encode_event(event, retention_days=self._retention_days))
            transaction.update(
                session_ref,
                {
                    "active_run_id": str(run.id),
                    "latest_run_state": RunState.REQUESTED.value,
                    "updated_at": now,
                    "expires_at": now + timedelta(days=self._retention_days),
                },
            )
            return run

        return cast(Run, transactional(reserve)(transaction))

    def get_run(self, user_id: str, session_id: str, run_id: UUID) -> Run:
        session = self.get_session(user_id, session_id)
        snapshot = (
            self._sessions(session.user_id)
            .document(session.id)
            .collection("runs")
            .document(str(run_id))
            .get()
        )
        if not snapshot.exists:
            raise SessionNotFoundError("run was not found")
        payload = dict(snapshot.to_dict())
        if is_expired(payload, self._now()):
            raise SessionNotFoundError("run has expired")
        run = self._decode_run(payload)
        if run.user_id != user_id or run.session_id != session_id:
            raise SessionOwnershipError("run belongs to another user")
        return run

    def save_execution(self, run_id: UUID, execution: ExecutionReference) -> Run:
        from google.cloud.firestore import transactional

        query = self._client.collection_group("runs").where("id", "==", str(run_id)).limit(1)
        found = list(query.stream())
        if not found:
            raise SessionNotFoundError("run was not found")
        reference = found[0].reference
        transaction = self._client.transaction()

        def save(transaction: Any) -> Run:
            snapshot = reference.get(transaction=transaction)
            current = self._decode_run(snapshot.to_dict())
            # RunJob may return only after the Job has already reached a terminal
            # state.  Store the execution reference, but never revive that run.
            state = RunState.PENDING if current.state is RunState.REQUESTED else current.state
            updated = current.model_copy(update={"execution": execution, "state": state})
            now = self._now()
            transaction.update(
                reference,
                {
                    "execution": execution.model_dump(),
                    "state": state.value,
                    "expires_at": now + timedelta(days=self._retention_days),
                },
            )
            return updated

        return cast(Run, transactional(save)(transaction))

    def fail_dispatch(self, run_id: UUID, error_code: str) -> Run:
        run = self.get_run_for_job(run_id).model_copy(
            update={"state": RunState.DISPATCH_FAILED, "error_code": error_code}
        )
        query = self._client.collection_group("runs").where("id", "==", str(run_id)).limit(1)
        found = list(query.stream())
        reference = found[0].reference
        now = self._now()
        reference.update(
            {
                "state": run.state.value,
                "error_code": error_code,
                "expires_at": now + timedelta(days=self._retention_days),
            }
        )
        reference.parent.parent.update(
            {
                "active_run_id": None,
                "latest_run_state": run.state.value,
                "updated_at": now,
                "expires_at": now + timedelta(days=self._retention_days),
            }
        )
        return run

    def get_run_for_job(self, run_id: UUID) -> Run:
        found = list(
            self._client.collection_group("runs").where("id", "==", str(run_id)).limit(1).stream()
        )
        if not found:
            raise SessionNotFoundError("run was not found")
        payload = dict(found[0].to_dict())
        if is_expired(payload, self._now()):
            raise SessionNotFoundError("run has expired")
        return self._decode_run(payload)

    def claim_run(self, run_id: UUID, execution_identity: str) -> bool:
        from google.cloud.firestore import transactional

        run_ref = self._client.collection_group("runs").where("id", "==", str(run_id)).limit(1)
        found = list(run_ref.stream())
        if not found:
            raise SessionNotFoundError("run was not found")
        reference = found[0].reference
        transaction = self._client.transaction()

        def claim(transaction: Any) -> bool:
            snapshot = reference.get(transaction=transaction)
            data = snapshot.to_dict()
            owner = data.get("execution_owner")
            if owner is not None and owner != execution_identity:
                return False
            transaction.update(
                reference,
                {
                    "execution_owner": execution_identity,
                    "state": RunState.RUNNING.value,
                    "heartbeat_at": self._now(),
                    "expires_at": self._now() + timedelta(days=self._retention_days),
                },
            )
            return True

        return cast(bool, transactional(claim)(transaction))

    def heartbeat_run(self, run_id: UUID, execution_identity: str) -> bool:
        query = self._client.collection_group("runs").where("id", "==", str(run_id)).limit(1)
        found = list(query.stream())
        if not found:
            raise SessionNotFoundError("run was not found")
        reference = found[0].reference
        transaction = self._client.transaction()

        def heartbeat(transaction: Any) -> bool:
            snapshot = reference.get(transaction=transaction)
            if snapshot.to_dict().get("execution_owner") != execution_identity:
                return False
            now = self._now()
            transaction.update(
                reference,
                {"heartbeat_at": now, "expires_at": now + timedelta(days=self._retention_days)},
            )
            return True

        from google.cloud.firestore import transactional

        return cast(bool, transactional(heartbeat)(transaction))

    def append_event(self, event: ChatEvent) -> ChatEvent:
        from google.cloud.firestore import transactional

        run_query = (
            self._client.collection_group("runs").where("id", "==", str(event.run_id)).limit(1)
        )
        found = list(run_query.stream())
        if not found:
            raise SessionNotFoundError("run was not found")
        run_ref = found[0].reference
        event_ref = run_ref.collection("events").document(event.id)
        transaction = self._client.transaction()

        def append(transaction: Any) -> ChatEvent:
            duplicate = event_ref.get(transaction=transaction)
            if duplicate.exists:
                value = dict(duplicate.to_dict())
                value.pop("schema_version", None)
                value.pop("expires_at", None)
                return ChatEvent.model_validate(value)
            run_snapshot = run_ref.get(transaction=transaction)
            next_sequence = int(run_snapshot.to_dict().get("next_sequence", 0))
            assigned = event.model_copy(update={"sequence": next_sequence})
            transaction.create(
                event_ref, encode_event(assigned, retention_days=self._retention_days)
            )
            now = self._now()
            transaction.update(
                run_ref,
                {
                    "next_sequence": next_sequence + 1,
                    "expires_at": now + timedelta(days=self._retention_days),
                },
            )
            return assigned

        return cast(ChatEvent, transactional(append)(transaction))

    def list_events(self, run_id: UUID, *, cursor: str | None = None) -> list[ChatEvent]:
        query = self._client.collection_group("runs").where("id", "==", str(run_id)).limit(1)
        found = list(query.stream())
        if not found:
            raise SessionNotFoundError("run was not found")
        run_payload = dict(found[0].to_dict())
        if is_expired(run_payload, self._now()):
            raise SessionNotFoundError("run has expired")
        snapshots = list(found[0].reference.collection("events").order_by("sequence").stream())
        events: list[ChatEvent] = []
        for snapshot in snapshots:
            payload = dict(snapshot.to_dict())
            if is_expired(payload, self._now()):
                continue
            payload.pop("schema_version", None)
            payload.pop("expires_at", None)
            event = ChatEvent.model_validate(payload)
            if cursor is None or event.id > cursor:
                events.append(event)
        return sorted(events, key=lambda event: (event.sequence, event.id))

    def subscribe(
        self, run_id: UUID, cursor: str | None, callback: Callable[[ChatEvent], None]
    ) -> Callable[[], None]:
        query = self._client.collection_group("runs").where("id", "==", str(run_id)).limit(1)
        found = list(query.stream())
        if not found:
            raise SessionNotFoundError("run was not found")
        if is_expired(dict(found[0].to_dict()), self._now()):
            raise SessionNotFoundError("run has expired")

        def on_snapshot(documents: Any, _changes: Any, _read_time: Any) -> None:
            for document in sorted(documents, key=lambda item: (item.get("sequence"), item.id)):
                payload = dict(document.to_dict())
                if is_expired(payload, self._now()):
                    continue
                payload.pop("schema_version", None)
                payload.pop("expires_at", None)
                event = ChatEvent.model_validate(payload)
                if cursor is None or event.id > cursor:
                    callback(event)

        event_query = found[0].reference.collection("events").order_by("sequence")
        watch = event_query.on_snapshot(on_snapshot)
        return cast(Callable[[], None], watch.unsubscribe)

    def request_cancel(self, run_id: UUID) -> Run:
        from google.cloud.firestore import transactional

        query = self._client.collection_group("runs").where("id", "==", str(run_id)).limit(1)
        found = list(query.stream())
        if not found:
            raise SessionNotFoundError("run was not found")
        reference = found[0].reference
        transaction = self._client.transaction()

        def request(transaction: Any) -> None:
            snapshot = reference.get(transaction=transaction)
            state = snapshot.to_dict().get("state")
            if state in {"completed", "failed", "cancelled", "timed_out", "dispatch_failed"}:
                return
            transaction.update(
                reference,
                {
                    "state": RunState.CANCEL_REQUESTED.value,
                    "cancel_requested_at": self._now(),
                    "expires_at": self._now() + timedelta(days=self._retention_days),
                },
            )

        transactional(request)(transaction)
        return self.get_run_for_job(run_id)

    def commit_terminal(self, run: Run, execution_identity: str) -> Run:
        if not run.state.terminal:
            raise ValueError("terminal state is required")
        from google.cloud.firestore import transactional

        query = self._client.collection_group("runs").where("id", "==", str(run.id)).limit(1)
        found = list(query.stream())
        if not found:
            raise SessionNotFoundError("run was not found")
        run_ref = found[0].reference
        session_ref = run_ref.parent.parent
        transaction = self._client.transaction()

        def commit(transaction: Any) -> Run:
            current = run_ref.get(transaction=transaction)
            if current.to_dict().get("execution_owner") != execution_identity:
                raise SessionOwnershipError("run belongs to another execution")
            now = self._now()
            transaction.update(
                run_ref, encode_run(run, retention_days=self._retention_days, now=now)
            )
            session_update: dict[str, Any] = {
                "active_run_id": None,
                "latest_run_state": run.state.value,
                "updated_at": now,
                "expires_at": now + timedelta(days=self._retention_days),
            }
            # Keep the last completed conversation when a later run fails;
            # otherwise a transient Job failure permanently breaks resume.
            if (
                run.state is RunState.COMPLETED
                and run.claude_session_id is not None
                and run.snapshot is not None
            ):
                session_update.update(
                    {
                        "claude_session_id": run.claude_session_id,
                        "snapshot": run.snapshot.model_dump(mode="json"),
                    }
                )
            transaction.update(session_ref, session_update)
            return run

        return cast(Run, transactional(commit)(transaction))

    def acquire_reconciliation_lease(
        self, run_id: UUID, holder: str, *, seconds: int = 30
    ) -> ReconciliationLease | None:
        from google.cloud.firestore import transactional

        query = self._client.collection_group("runs").where("id", "==", str(run_id)).limit(1)
        found = list(query.stream())
        if not found:
            raise SessionNotFoundError("run was not found")
        lease_ref = found[0].reference.collection("leases").document("reconciliation")
        transaction = self._client.transaction()

        def acquire(transaction: Any) -> ReconciliationLease | None:
            snapshot = lease_ref.get(transaction=transaction)
            now = self._now()
            if snapshot.exists:
                current = snapshot.to_dict()
                if current.get("holder") != holder and current.get("expires_at") > now:
                    return None
            expires_at = datetime.fromtimestamp(now.timestamp() + seconds, UTC)
            transaction.set(lease_ref, {"holder": holder, "expires_at": expires_at})
            return ReconciliationLease(run_id=run_id, holder=holder, expires_at=expires_at)

        return cast(ReconciliationLease | None, transactional(acquire)(transaction))

    def reconcile_terminal(
        self, run_id: UUID, holder: str, state: RunState, *, error_code: str | None = None
    ) -> Run:
        from google.cloud.firestore import transactional

        if not state.terminal:
            raise ValueError("terminal state is required")
        lease = self.acquire_reconciliation_lease(run_id, holder)
        if lease is None:
            raise SessionOwnershipError("reconciliation lease is not held")
        query = self._client.collection_group("runs").where("id", "==", str(run_id)).limit(1)
        found = list(query.stream())
        ref = found[0].reference
        session_ref = ref.parent.parent
        transaction = self._client.transaction()

        def commit(transaction: Any) -> Run:
            snapshot = ref.get(transaction=transaction)
            current = self._decode_run(dict(snapshot.to_dict()))
            if current.state.terminal:
                return current
            now = self._now()
            run = current.model_copy(
                update={"state": state, "error_code": error_code, "finished_at": now}
            )
            transaction.update(
                ref,
                {
                    "state": state.value,
                    "error_code": error_code,
                    "finished_at": now,
                    "expires_at": now + timedelta(days=self._retention_days),
                },
            )
            transaction.update(
                session_ref,
                {
                    "active_run_id": None,
                    "latest_run_state": state.value,
                    "updated_at": now,
                    "expires_at": now + timedelta(days=self._retention_days),
                },
            )
            return run

        return cast(Run, transactional(commit)(transaction))

    def release_reconciliation_lease(self, run_id: UUID, holder: str) -> None:
        query = self._client.collection_group("runs").where("id", "==", str(run_id)).limit(1)
        found = list(query.stream())
        if not found:
            raise SessionNotFoundError("run was not found")
        lease_ref = found[0].reference.collection("leases").document("reconciliation")
        snapshot = lease_ref.get()
        if snapshot.exists and snapshot.to_dict().get("holder") == holder:
            lease_ref.delete()

    def update_session_summary(
        self,
        user_id: str,
        session_id: str,
        *,
        title: str | None = None,
        latest_run_state: str | None = None,
        active_run_id: str | None = None,
    ) -> None:
        self.get_session(user_id, session_id)
        now = self._now()
        updates: dict[str, object] = {
            "updated_at": now,
            "expires_at": now + timedelta(days=self._retention_days),
        }
        if title is not None:
            updates["title"] = title
        if latest_run_state is not None:
            updates["latest_run_state"] = latest_run_state
        if active_run_id is not None:
            updates["active_run_id"] = active_run_id
        self._sessions(user_id).document(session_id).update(updates)

    @staticmethod
    def _decode_session(payload: dict[str, object]) -> Session:
        value = dict(payload)
        value.pop("schema_version", None)
        value.pop("expires_at", None)
        return Session.model_validate(value)

    @staticmethod
    def _decode_run(payload: dict[str, object]) -> Run:
        value = dict(payload)
        for field in (
            "schema_version",
            "expires_at",
            "next_sequence",
            "execution_owner",
            "heartbeat_at",
            "cancel_requested_at",
        ):
            value.pop(field, None)
        return Run.model_validate(value)

    @staticmethod
    def _encode_cursor(updated_at: datetime, session_id: str) -> str:
        payload = json.dumps([updated_at.isoformat(), session_id]).encode()
        return base64.urlsafe_b64encode(payload).decode()

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[datetime, str]:
        try:
            timestamp, session_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            return datetime.fromisoformat(timestamp).astimezone(UTC), str(session_id)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("invalid session cursor") from error

    @staticmethod
    def _encode_run_cursor(created_at: datetime, run_id: str) -> str:
        payload = json.dumps([created_at.isoformat(), run_id]).encode()
        return base64.urlsafe_b64encode(payload).decode()

    @staticmethod
    def _decode_run_cursor(cursor: str) -> tuple[datetime, str]:
        try:
            timestamp, run_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            return decode_timestamp(timestamp), str(run_id)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("invalid run cursor") from error

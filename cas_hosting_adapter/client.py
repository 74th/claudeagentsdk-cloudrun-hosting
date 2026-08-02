"""High-level client boundary for sessions and async Agent Platform invocations."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from .errors import ValidationError
from .models import AsyncRun, HostingSettings, RunIdentifiers, RunStatus, SessionEvent
from .protocols import Operations, SnapshotStore
from .session_store import GoogleSessionStore
from .workspace_store import RunLockStore, StoragePaths


class HostingClient:
    """Coordinates the Session, GCS lock, and Agent Platform boundaries."""

    def __init__(
        self,
        *,
        session_store: GoogleSessionStore,
        snapshot_store: SnapshotStore,
        operations: Operations,
        settings: HostingSettings,
        gcs_uri: Any,
    ) -> None:
        self.session_store = session_store
        self.snapshot_store = snapshot_store
        self.operations = operations
        self.settings = settings
        self.gcs_uri = gcs_uri
        self.locks = RunLockStore(snapshot_store)

    def create_session(self, user_id: str) -> str:
        return self.session_store.create(user_id)

    def get_session(self, session_id: str, user_id: str) -> Any:
        return self.session_store.get(session_id, user_id)

    def list_sessions(self, user_id: str) -> list[Any]:
        return self.session_store.list_for_user(user_id)

    def start_run(self, *, user_id: str, message: str, session_id: str | None = None) -> AsyncRun:
        if (
            not user_id.strip()
            or not message.strip()
            or len(message) > self.settings.max_message_chars
        ):
            raise ValidationError("user_id and message must be valid")
        if session_id is None:
            session_id = self.create_session(user_id)
        else:
            self.get_session(session_id, user_id)

        run_id = uuid4()
        paths = StoragePaths.for_session(
            user_id=user_id,
            session_id=session_id,
            schema_version=self.settings.schema_version,
            sdk_version=self.settings.claude_sdk_version,
        )
        lock, generation = self.locks.acquire(
            paths.lock_path, run_id, pending_ttl=timedelta(minutes=2)
        )
        operation_started = False
        try:
            payload = {
                "user_id": user_id,
                "session_id": session_id,
                "run_id": str(run_id),
                "workspace_id": paths.session_hash,
                "message": message,
            }
            self.snapshot_store.upload(
                paths.input_path(run_id), json.dumps(payload, sort_keys=True).encode(),
                if_generation_match=0,
            )
            self.session_store.append(
                session_id,
                SessionEvent(
                    run_id=run_id, sequence=0, event_type="run_requested",
                    payload={"input_path": paths.input_path(run_id)},
                ),
            )
            operation_name = self.operations.start(
                input_payload={
                    # run_query_job accepts an operation's input object.  The
                    # Agent Platform SDK constructs the runtime envelope itself.
                    "query": json.dumps({"input": payload}, sort_keys=True),
                    "output_gcs_uri": self.gcs_uri(paths.output_path(run_id)),
                }
            )
            operation_started = True
            _, generation = self.locks.bind_operation(
                paths.lock_path, generation, lock,
                operation_name=operation_name,
                running_ttl=timedelta(seconds=self.settings.max_execution_seconds + 60),
            )
            self.session_store.append(
                session_id,
                SessionEvent(
                    run_id=run_id, sequence=1, event_type="operation_bound",
                    payload={
                        "operation_name": operation_name,
                        "lock_path": paths.lock_path,
                        "lock_generation": generation,
                    },
                ),
            )
            return AsyncRun(
                session_id=session_id, run_id=run_id, operation_name=operation_name,
                workspace_id=paths.session_hash,
            )
        except Exception:
            # Once Agent Platform accepted the operation, retain its lock for later
            # reconciliation rather than allowing a conflicting run to start.
            if not operation_started:
                self.locks.release(paths.lock_path, generation)
            raise

    def get_run_status(self, *, user_id: str, session_id: str, run_id: UUID) -> RunStatus:
        self.get_session(session_id, user_id)
        events = self.session_store.events_for_run(session_id, run_id)
        operation_name = next(
            (
                event.payload.get("operation_name")
                for event in reversed(events)
                if event.event_type == "operation_bound"
                and isinstance(event.payload.get("operation_name"), str)
            ),
            None,
        )
        operation_status = self.operations.get(operation_name) if operation_name else None
        state = self.session_store.reconcile_run(
            session_id, run_id, operation_status=operation_status
        )
        if state.value == "cancelled" and not any(
            event.event_type == "cancelled" for event in events
        ):
            binding = next(
                (event for event in reversed(events) if event.event_type == "operation_bound"),
                None,
            )
            next_sequence = max((event.sequence for event in events), default=-1) + 1
            self.session_store.append(
                session_id,
                SessionEvent(
                    run_id=run_id, sequence=next_sequence, event_type="cancelled",
                    payload={"operation_name": operation_name},
                ),
            )
            if binding is not None:
                lock_path = binding.payload.get("lock_path")
                lock_generation = binding.payload.get("lock_generation")
                if isinstance(lock_path, str) and isinstance(lock_generation, int):
                    try:
                        self.locks.release(lock_path, lock_generation)
                    except FileNotFoundError:
                        pass
            events = self.session_store.events_for_run(session_id, run_id)
        paths = StoragePaths.for_session(
            user_id=user_id, session_id=session_id,
            schema_version=self.settings.schema_version,
            sdk_version=self.settings.claude_sdk_version,
        )
        output = next(
            (
                event.payload.get("output")
                for event in reversed(events)
                if event.event_type == "completed" and isinstance(event.payload.get("output"), str)
            ),
            None,
        )
        return RunStatus(
            identifiers=RunIdentifiers(
                session_id=session_id, run_id=run_id, operation_name=operation_name,
                workspace_id=paths.session_hash,
            ),
            state=state, events=events, output=output,
            error_code="persistence_failed" if state.value == "persistence_failed" else None,
        )

    def cancel_run(self, *, user_id: str, session_id: str, run_id: UUID) -> None:
        status = self.get_run_status(user_id=user_id, session_id=session_id, run_id=run_id)
        operation_name = status.identifiers.operation_name
        if operation_name is None:
            raise ValidationError("run has no Agent Platform operation")
        self.operations.cancel(operation_name)
        next_sequence = max((event.sequence for event in status.events), default=-1) + 1
        self.session_store.append(
            session_id,
            SessionEvent(
                run_id=run_id, sequence=next_sequence, event_type="cancel_requested",
                payload={"operation_name": operation_name},
            ),
        )

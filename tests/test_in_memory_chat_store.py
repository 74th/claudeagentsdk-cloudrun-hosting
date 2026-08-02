from cas_hosting_adapter.in_memory_chat_store import InMemoryChatStore
from cas_hosting_adapter.models import ChatEvent, Run, Session


def test_reservation_and_events_are_idempotent() -> None:
    store = InMemoryChatStore()
    session = store.put_session(
        Session(id="session", user_id="user", workspace_id="workspace")
    )
    run = Run(
        user_id="user", session_id=session.id, workspace_id="workspace", idempotency_key="key"
    )
    event = ChatEvent(id="message", run_id=run.id, sequence=0, type="user")
    assert store.reserve_run(run, event) == store.reserve_run(run, event)
    appended = store.append_event(ChatEvent(id="agent", run_id=run.id, sequence=99, type="agent"))
    assert appended.sequence == 1
    assert store.append_event(appended) == appended

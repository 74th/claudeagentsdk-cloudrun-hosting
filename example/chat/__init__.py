"""フロントエンド非依存の会話サービス。"""

from .events import (
    ChatEventKind,
    CommonChatEvent,
    InteractionState,
    TaskState,
    interaction_state,
    normalize_event,
    normalize_events,
    reduce_tasks,
)
from .identity import IdentityProvider, StaticIdentity
from .service import ChatService, ChatStartResult, create_control_client_from_release_config

__all__ = [
    "ChatEventKind",
    "ChatService",
    "ChatStartResult",
    "CommonChatEvent",
    "InteractionState",
    "TaskState",
    "IdentityProvider",
    "StaticIdentity",
    "create_control_client_from_release_config",
    "normalize_event",
    "normalize_events",
    "interaction_state",
    "reduce_tasks",
]

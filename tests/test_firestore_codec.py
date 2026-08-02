from uuid import uuid4

import pytest

from cas_hosting_adapter.firestore_codec import encode_event, user_key
from cas_hosting_adapter.models import ChatEvent


def test_user_key_is_normalized_and_never_a_document_path() -> None:
    assert user_key(" alice ") == user_key("alice")
    assert len(user_key("a/b")) == 64
    with pytest.raises(ValueError):
        user_key("  ")


def test_event_codec_is_versioned() -> None:
    encoded = encode_event(ChatEvent(id="event", run_id=uuid4(), sequence=0, type="user"))
    assert encoded["schema_version"] == "1"

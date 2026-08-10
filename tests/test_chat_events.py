from __future__ import annotations

from math import inf, nan
from uuid import uuid4

from cas_hosting_adapter.models import ChatEvent
from example.chat.events import (
    ProcessingMetadata,
    format_processing_metadata,
    normalize_event,
    processing_metadata,
)


def test_processing_metadata_accepts_finite_non_negative_values() -> None:
    metadata = processing_metadata({"estimated_cost_usd": 0.012345, "duration_ms": 1234})

    assert metadata == ProcessingMetadata(estimated_cost_usd=0.012345, duration_ms=1234)
    assert format_processing_metadata(metadata) == (
        "推定価格 (USD): $0.012345\n処理時間 (SDK): 1.23秒"
    )


def test_processing_metadata_omits_invalid_values_without_zero_fill() -> None:
    metadata = processing_metadata(
        {
            "estimated_cost_usd": -1,
            "duration_ms": True,
            "unknown": "kept in the raw payload",
        }
    )

    assert metadata == ProcessingMetadata()
    assert format_processing_metadata(metadata) == ""


def test_processing_metadata_rejects_non_finite_values_and_old_events() -> None:
    assert processing_metadata({"estimated_cost_usd": nan, "duration_ms": inf}) == (
        ProcessingMetadata()
    )
    event = normalize_event(
        ChatEvent(
            id="legacy-final",
            run_id=uuid4(),
            sequence=1,
            type="final",
            payload={"output": "old"},
        )
    )
    assert event.processing_metadata == ProcessingMetadata()
    assert event.content == "old"

from datetime import UTC, datetime

from scripts.backfill_firestore_ttl import chunked, legacy_base_timestamp


def test_backfill_chooses_collection_specific_legacy_timestamp() -> None:
    value = datetime(2026, 1, 1, tzinfo=UTC)
    assert legacy_base_timestamp({"updated_at": value}, "sessions") == value
    assert legacy_base_timestamp({"created_at": value}, "runs") == value
    assert legacy_base_timestamp({"occurred_at": value}, "events") == value


def test_backfill_batches_are_bounded() -> None:
    assert list(chunked(range(5), 2)) == [[0, 1], [2, 3], [4]]

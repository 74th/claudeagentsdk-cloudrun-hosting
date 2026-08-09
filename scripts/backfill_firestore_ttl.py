"""Backfill Firestore TTL fields for legacy chat documents.

The command requires an explicit project and named database and is dry-run by
default.  It intentionally updates only documents that do not have
``expires_at`` so repeated apply runs are idempotent.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from cas_hosting_adapter.firestore_codec import decode_timestamp

COLLECTIONS = ("sessions", "runs", "events")


def legacy_base_timestamp(payload: dict[str, Any], collection: str) -> datetime:
    """Choose the original activity timestamp used to calculate the TTL."""
    fields = {
        "sessions": ("updated_at", "created_at"),
        "runs": ("created_at", "finished_at"),
        "events": ("occurred_at", "created_at", "timestamp"),
    }
    for field in fields[collection]:
        value = payload.get(field)
        if value is not None:
            return decode_timestamp(value)
    raise ValueError(f"{collection} document has no usable legacy timestamp")


def chunked(items: Iterable[Any], size: int) -> Iterator[list[Any]]:
    if size < 1:
        raise ValueError("batch size must be positive")
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def backfill_collection(
    client: Any,
    collection: str,
    *,
    retention_days: int,
    batch_size: int,
    apply: bool,
) -> dict[str, int]:
    if collection not in COLLECTIONS:
        raise ValueError(f"unsupported collection group: {collection}")
    if retention_days < 1:
        raise ValueError("retention_days must be positive")
    candidates: list[tuple[Any, datetime]] = []
    skipped = 0
    for snapshot in client.collection_group(collection).stream():
        payload = dict(snapshot.to_dict() or {})
        if payload.get("expires_at") is not None:
            skipped += 1
            continue
        base = legacy_base_timestamp(payload, collection)
        candidates.append((snapshot.reference, base + timedelta(days=retention_days)))

    updated = 0
    if apply:
        for batch_items in chunked(candidates, batch_size):
            batch = client.batch()
            for reference, expires_at in batch_items:
                batch.update(reference, {"expires_at": expires_at.astimezone(UTC)})
            batch.commit()
            updated += len(batch_items)
    return {"candidates": len(candidates), "updated": updated, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Explicit Google Cloud project ID")
    parser.add_argument("--database", required=True, help="Explicit named Firestore database ID")
    parser.add_argument("--retention-days", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--apply", action="store_true", help="Write updates; default is dry-run")
    args = parser.parse_args()
    if args.database == "(default)":
        parser.error("--database must name a database, not (default)")

    from google.cloud.firestore import Client

    client = Client(project=args.project, database=args.database)
    summary = {
        collection: backfill_collection(
            client,
            collection,
            retention_days=args.retention_days,
            batch_size=args.batch_size,
            apply=args.apply,
        )
        for collection in COLLECTIONS
    }
    print(
        json.dumps(
            {
                "dry_run": not args.apply,
                "project": args.project,
                "database": args.database,
                "collections": summary,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

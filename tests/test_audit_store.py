"""Tests for the audit store (SE-15a), against a real MongoDB.

Skipped unless MONGO_URL is set. Locally, with docker compose running:

    export MONGO_URL=mongodb://settlement:settlement-dev@localhost:27017
"""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo import MongoClient
from pymongo.collection import Collection

from settlement.domain.errors import InvalidModel
from settlement.domain.models import AuditEvent
from settlement.storage.audit import COLLECTION, AuditStore
from settlement.storage.errors import AuditStoreError


@pytest.fixture
def collection() -> Iterator[Collection[dict[str, Any]]]:
    """A fresh, empty collection in a throwaway database, dropped afterwards."""
    url = os.environ.get("MONGO_URL")
    if not url:
        pytest.skip("MONGO_URL not set; start MongoDB with docker compose to run this")
    client: MongoClient[dict[str, Any]] = MongoClient(url, tz_aware=True)
    database = f"test_audit_{uuid.uuid4().hex[:8]}"
    yield client[database][COLLECTION]
    client.drop_database(database)
    client.close()


@pytest.fixture
def store(collection: Collection[dict[str, Any]]) -> AuditStore:
    return AuditStore(collection)


def event(
    event_id: str,
    hour: int,
    entity_id: str = "T-1",
    before: str | None = "BOOKED",
    after: str | None = "ALLOCATED",
) -> AuditEvent:
    return AuditEvent(
        event_id=event_id,
        entity_type="Trade",
        entity_id=entity_id,
        event_type="STATUS_CHANGED",
        actor="system",
        occurred_at=datetime(2026, 10, 6, hour, 0, tzinfo=UTC),
        before=before,
        after=after,
    )


def test_an_event_comes_back_exactly_as_it_went_in(store: AuditStore) -> None:
    original = event("E-1", 9)

    store.append(original)

    assert store.events_for("Trade", "T-1") == [original]


def test_timestamps_keep_their_timezone(store: AuditStore) -> None:
    store.append(event("E-1", 9))

    (read_back,) = store.events_for("Trade", "T-1")

    assert read_back.occurred_at.tzinfo is not None
    assert read_back.occurred_at == datetime(2026, 10, 6, 9, 0, tzinfo=UTC)


def test_events_come_back_oldest_first(store: AuditStore) -> None:
    later, earlier = event("E-2", 11), event("E-1", 9)
    store.append(later)  # saved out of order on purpose
    store.append(earlier)

    assert store.events_for("Trade", "T-1") == [earlier, later]


def test_same_time_is_ordered_by_event_id(store: AuditStore) -> None:
    store.append(event("E-B", 9))
    store.append(event("E-A", 9))

    assert [e.event_id for e in store.events_for("Trade", "T-1")] == ["E-A", "E-B"]


def test_only_that_records_events_come_back(store: AuditStore) -> None:
    mine, theirs = event("E-1", 9, entity_id="T-1"), event("E-2", 9, entity_id="T-2")
    store.append(mine)
    store.append(theirs)

    assert store.events_for("Trade", "T-1") == [mine]
    assert store.events_for("Break", "T-1") == []  # same id, different kind of record


def test_a_record_with_no_events_gives_an_empty_list(store: AuditStore) -> None:
    assert store.events_for("Trade", "T-NOTHING") == []


def test_optional_before_and_after_survive(store: AuditStore) -> None:
    raised = event("E-1", 9, before=None, after=None)
    store.append(raised)

    assert store.events_for("Trade", "T-1") == [raised]


def test_recording_an_event_twice_is_refused(store: AuditStore) -> None:
    original = event("E-1", 9)
    store.append(original)

    with pytest.raises(AuditStoreError, match="'E-1' is already recorded"):
        store.append(replace_actor(original, "someone-else"))

    assert store.events_for("Trade", "T-1") == [original]  # the first one is untouched


def test_a_corrupt_document_is_caught_when_read(
    store: AuditStore, collection: Collection[dict[str, Any]]
) -> None:
    """Reading rebuilds an AuditEvent, so its validation runs on the way out too."""
    store.append(event("E-1", 9))
    collection.insert_one(
        {
            "_id": "E-BAD",
            "entity_type": "Trade",
            "entity_id": "T-1",
            "event_type": "STATUS_CHANGED",
            "actor": "",  # an event must say who did it
            "occurred_at": datetime(2026, 10, 6, 10, 0, tzinfo=UTC),
            "before": None,
            "after": None,
        }
    )

    with pytest.raises(InvalidModel, match="actor"):
        store.events_for("Trade", "T-1")


def test_the_store_has_no_way_to_change_or_remove_events() -> None:
    """Level 1 of append-only: our own code can't do it. (Level 2, the database, is SE-15b.)"""
    methods = {name for name in dir(AuditStore) if not name.startswith("_")}

    assert methods == {"connect", "append", "events_for"}


def replace_actor(original: AuditEvent, actor: str) -> AuditEvent:
    return AuditEvent(
        event_id=original.event_id,
        entity_type=original.entity_type,
        entity_id=original.entity_id,
        event_type=original.event_type,
        actor=actor,
        occurred_at=original.occurred_at,
        before=original.before,
        after=original.after,
    )

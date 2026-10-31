"""Tests for the audit store (SE-15a), against a real MongoDB.

Skipped unless MONGO_URL is set. Locally, with docker compose running:

    export MONGO_URL=mongodb://settlement:settlement-dev@localhost:27017
"""

import os
import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import WriteError

from settlement.domain.models import AuditEvent
from settlement.storage.audit import COLLECTION, AuditStore, set_up_collection
from settlement.storage.errors import AuditStoreError


@pytest.fixture
def collection() -> Iterator[Collection[dict[str, Any]]]:
    """A fresh, empty collection in a throwaway database, dropped afterwards."""
    url = os.environ.get("MONGO_URL")
    if not url:
        pytest.skip("MONGO_URL not set; start MongoDB with docker compose to run this")
    client: MongoClient[dict[str, Any]] = MongoClient(url, tz_aware=True)
    database = f"test_audit_{uuid.uuid4().hex[:8]}"
    set_up_collection(client[database])
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

    stored = store.append(original)

    assert stored == replace(original, sequence=1)
    assert store.events_for("Trade", "T-1") == [stored]


def test_timestamps_keep_their_timezone(store: AuditStore) -> None:
    store.append(event("E-1", 9))

    (read_back,) = store.events_for("Trade", "T-1")

    assert read_back.occurred_at.tzinfo is not None
    assert read_back.occurred_at == datetime(2026, 10, 6, 9, 0, tzinfo=UTC)


def test_events_are_numbered_in_the_order_they_were_written(store: AuditStore) -> None:
    first, second = store.append(event("E-1", 9)), store.append(event("E-2", 11))

    assert (first.sequence, second.sequence) == (1, 2)
    assert store.events_for("Trade", "T-1") == [first, second]


def test_each_record_is_numbered_on_its_own(store: AuditStore) -> None:
    store.append(event("E-1", 9, entity_id="T-1"))

    assert store.append(event("E-2", 9, entity_id="T-2")).sequence == 1


def test_same_time_keeps_the_order_written_not_the_id_as_text(store: AuditStore) -> None:
    """As text, "E-10" sorts before "E-9". The sequence doesn't care what the id says."""
    store.append(event("E-9", 9))
    store.append(event("E-10", 9))

    assert [e.event_id for e in store.events_for("Trade", "T-1")] == ["E-9", "E-10"]


def test_losing_a_race_to_the_same_position_is_refused(
    store: AuditStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two writers both read "highest is 1" and both try to write #2: only one may win."""
    store.append(event("E-1", 9))
    winner = store.append(event("E-2", 10))
    monkeypatch.setattr(store, "_next_sequence", lambda _event: 2)  # the loser's stale read

    with pytest.raises(AuditStoreError, match="'T-1' changed while event 'E-3'"):
        store.append(event("E-3", 10))

    assert store.events_for("Trade", "T-1")[-1] == winner


def test_an_event_that_already_has_a_sequence_is_refused(store: AuditStore) -> None:
    with pytest.raises(AuditStoreError, match="the store numbers events itself"):
        store.append(replace(event("E-1", 9), sequence=5))


def test_only_that_records_events_come_back(store: AuditStore) -> None:
    mine = store.append(event("E-1", 9, entity_id="T-1"))
    store.append(event("E-2", 9, entity_id="T-2"))

    assert store.events_for("Trade", "T-1") == [mine]
    assert store.events_for("Break", "T-1") == []  # same id, different kind of record


def test_a_record_with_no_events_gives_an_empty_list(store: AuditStore) -> None:
    assert store.events_for("Trade", "T-NOTHING") == []


def test_optional_before_and_after_survive(store: AuditStore) -> None:
    raised = store.append(event("E-1", 9, before=None, after=None))

    assert store.events_for("Trade", "T-1") == [raised]


def test_recording_an_event_twice_is_refused(store: AuditStore) -> None:
    original = event("E-1", 9)
    stored = store.append(original)

    with pytest.raises(AuditStoreError, match="'E-1' is already recorded"):
        store.append(replace_actor(original, "someone-else"))

    assert store.events_for("Trade", "T-1") == [stored]  # the first one is untouched


DOCUMENT_VALIDATION_FAILED = 121  # MongoDB's error code when the validator refuses a write


def a_document(**changes: Any) -> dict[str, Any]:
    """A well-formed audit document, with `changes` applied (None removes a field)."""
    document: dict[str, Any] = {
        "_id": "E-BAD",
        "entity_type": "Trade",
        "entity_id": "T-1",
        "event_type": "STATUS_CHANGED",
        "actor": "system",
        "occurred_at": datetime(2026, 10, 6, 10, 0, tzinfo=UTC),
        "before": "BOOKED",
        "after": "ALLOCATED",
        "sequence": 1,
    }
    document.update(changes)
    return {name: value for name, value in document.items() if value is not None}


@pytest.mark.parametrize(
    "changes",
    [
        {"actor": None},  # a required field missing
        {"actor": ""},  # empty text
        {"sequence": 0},  # positions start at 1
        {"sequence": "1"},  # wrong type
        {"occurred_at": "2026-10-06T10:00:00Z"},  # a string, not a date
        {"extra": "field"},  # nothing the event doesn't have
    ],
)
def test_the_validator_refuses_a_malformed_document(
    collection: Collection[dict[str, Any]], changes: dict[str, Any]
) -> None:
    with pytest.raises(WriteError) as error:
        collection.insert_one(a_document(**changes))

    assert error.value.code == DOCUMENT_VALIDATION_FAILED


def test_the_validator_accepts_a_well_formed_document(
    collection: Collection[dict[str, Any]],
) -> None:
    collection.insert_one(a_document())


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"actor": None}, "'E-BAD' is missing field 'actor'"),
        ({"actor": ""}, "'E-BAD' is invalid: actor must not be empty"),
    ],
)
def test_a_malformed_document_that_got_in_anyway_is_reported_by_id(
    store: AuditStore,
    collection: Collection[dict[str, Any]],
    changes: dict[str, Any],
    message: str,
) -> None:
    """E.g. written before the validator existed. An admin can skip it; the app can't."""
    collection.insert_one(a_document(**changes), bypass_document_validation=True)

    with pytest.raises(AuditStoreError, match=message):
        store.events_for("Trade", "T-1")


def test_the_store_has_no_way_to_change_or_remove_events() -> None:
    """Level 1 of append-only: our own code can't do it. (Level 2, the database, is SE-15b.)"""
    methods = {name for name in dir(AuditStore) if not name.startswith("_")}

    assert methods == {"connect", "append", "events_for", "state_as_of"}  # no update, no delete


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

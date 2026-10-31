"""Append-only audit store in MongoDB (SE-15): every event is written once and never changed."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from pymongo import DESCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from settlement.domain import history
from settlement.domain.errors import InvalidModel
from settlement.domain.models import AuditEvent
from settlement.storage.errors import AuditStoreError

COLLECTION = "audit_events"

# One event per position in a record's history. Two writers that both try to add event #4
# to the same record: MongoDB accepts the first and refuses the second.
_POSITION_INDEX = "one_event_per_position"
_POSITION_KEYS = [("entity_type", 1), ("entity_id", 1), ("sequence", 1)]


_TEXT = {"bsonType": "string", "minLength": 1}
_STATE = {"bsonType": ["string", "null"]}

# The shape of every audit document. MongoDB refuses an insert that doesn't fit, so a
# malformed event can't get in and make its record unreadable. The append-only role has
# no bypassDocumentValidation, so the app can't switch this off.
SCHEMA: Mapping[str, Any] = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": [
            "_id",
            "entity_type",
            "entity_id",
            "event_type",
            "actor",
            "occurred_at",
            "before",
            "after",
            "sequence",
        ],
        "additionalProperties": False,
        "properties": {
            "_id": _TEXT,
            "entity_type": _TEXT,
            "entity_id": _TEXT,
            "event_type": _TEXT,
            "actor": _TEXT,
            "occurred_at": {"bsonType": "date"},
            "before": _STATE,
            "after": _STATE,
            "sequence": {"bsonType": ["int", "long"], "minimum": 1},
        },
    }
}


def set_up_collection(database: Database[dict[str, Any]]) -> None:
    """Create the audit collection with its validator and unique position index.

    Needs an admin login. Safe to run again: an existing collection gets the current
    validator, and an existing index is left alone.
    """
    if COLLECTION in database.list_collection_names():
        database.command("collMod", COLLECTION, validator=SCHEMA, validationLevel="strict")
    else:
        database.create_collection(COLLECTION, validator=SCHEMA, validationLevel="strict")
    database[COLLECTION].create_index(_POSITION_KEYS, name=_POSITION_INDEX, unique=True)


class AuditStore:
    """Events go in and come out. There is deliberately no update and no delete."""

    def __init__(self, collection: Collection[dict[str, Any]]) -> None:
        self._events = collection

    @classmethod
    def connect(cls, url: str, database: str = "settlement") -> "AuditStore":
        # tz_aware: without it MongoDB hands back timestamps with no timezone, which
        # AuditEvent rejects.
        client: MongoClient[dict[str, Any]] = MongoClient(url, tz_aware=True)
        return cls(client[database][COLLECTION])

    def append(self, event: AuditEvent) -> AuditEvent:
        """Record an event as the next one in its record's history, and return it numbered.

        The same event_id twice is an error, never a silent overwrite. So is losing a race:
        if another writer added an event to this record in the meantime, this one was built
        from an out-of-date state, and the caller has to re-read and try again.
        """
        if event.sequence is not None:
            raise AuditStoreError(
                f"event {event.event_id!r} already has sequence {event.sequence}; "
                "the store numbers events itself"
            )
        numbered = replace(event, sequence=self._next_sequence(event))
        try:
            self._events.insert_one(_to_document(numbered))
        except DuplicateKeyError as error:
            if "_id" in (error.details or {}).get("keyPattern", {}):
                raise AuditStoreError(f"event {event.event_id!r} is already recorded") from None
            raise AuditStoreError(
                f"{event.entity_type} {event.entity_id!r} changed while event "
                f"{event.event_id!r} was being written; re-read it and try again"
            ) from None
        return numbered

    def events_for(self, entity_type: str, entity_id: str) -> list[AuditEvent]:
        """Every event for one record, in the order they were written."""
        documents = self._events.find({"entity_type": entity_type, "entity_id": entity_id})
        return [_from_document(doc) for doc in documents.sort("sequence", 1)]

    def state_as_of(
        self, entity_type: str, entity_id: str, at: datetime
    ) -> history.StateAsOf | None:
        """What a record looked like at `at`, rebuilt from its events alone."""
        return history.state_as_of(self.events_for(entity_type, entity_id), at)

    def _next_sequence(self, event: AuditEvent) -> int:
        """One more than the highest sequence this record has, or 1 for its first event."""
        latest = self._events.find_one(
            {"entity_type": event.entity_type, "entity_id": event.entity_id},
            sort=[("sequence", DESCENDING)],
        )
        return 1 if latest is None else int(latest["sequence"]) + 1


def _to_document(event: AuditEvent) -> dict[str, Any]:
    """An AuditEvent as a MongoDB document. Its event_id becomes _id, which MongoDB keeps unique."""
    return {
        "_id": event.event_id,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "event_type": event.event_type,
        "actor": event.actor,
        "occurred_at": event.occurred_at,
        "before": event.before,
        "after": event.after,
        "sequence": event.sequence,
    }


def _from_document(document: Mapping[str, Any]) -> AuditEvent:
    """A MongoDB document back into an AuditEvent, so all of its validation runs again.

    The validator keeps malformed documents out, but one written before it existed (or by
    an admin, who can bypass it) is reported by its id rather than as a bare KeyError.
    """
    try:
        return AuditEvent(
            event_id=document["_id"],
            entity_type=document["entity_type"],
            entity_id=document["entity_id"],
            event_type=document["event_type"],
            actor=document["actor"],
            occurred_at=document["occurred_at"],
            before=document["before"],
            after=document["after"],
            sequence=document["sequence"],
        )
    except KeyError as error:
        raise AuditStoreError(
            f"audit document {document.get('_id')!r} is missing field {error}"
        ) from None
    except InvalidModel as error:
        raise AuditStoreError(
            f"audit document {document.get('_id')!r} is invalid: {error}"
        ) from None

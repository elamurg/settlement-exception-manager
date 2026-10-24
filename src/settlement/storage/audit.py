"""Append-only audit store in MongoDB (SE-15): every event is written once and never changed."""

from collections.abc import Mapping
from typing import Any

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError

from settlement.domain.models import AuditEvent
from settlement.storage.errors import AuditStoreError

COLLECTION = "audit_events"


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

    def append(self, event: AuditEvent) -> None:
        """Record an event. The same event_id twice is an error, never a silent overwrite."""
        try:
            self._events.insert_one(_to_document(event))
        except DuplicateKeyError:
            raise AuditStoreError(f"event {event.event_id!r} is already recorded") from None

    def events_for(self, entity_type: str, entity_id: str) -> list[AuditEvent]:
        """Every event for one record, oldest first."""
        documents = self._events.find({"entity_type": entity_type, "entity_id": entity_id})
        return [_from_document(doc) for doc in documents.sort([("occurred_at", 1), ("_id", 1)])]


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
    }


def _from_document(document: Mapping[str, Any]) -> AuditEvent:
    """A MongoDB document back into an AuditEvent, so all of its validation runs again."""
    return AuditEvent(
        event_id=document["_id"],
        entity_type=document["entity_type"],
        entity_id=document["entity_id"],
        event_type=document["event_type"],
        actor=document["actor"],
        occurred_at=document["occurred_at"],
        before=document["before"],
        after=document["after"],
    )

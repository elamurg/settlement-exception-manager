"""Tests for database-level append-only access (SE-15b), against a real MongoDB.

MONGO_URL must be an admin login (the root user from docker compose). Each test creates a
throwaway database, role and user, and removes them afterwards.
"""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import OperationFailure
from pymongo.uri_parser import parse_uri

from settlement.domain.models import AuditEvent
from settlement.storage.access import ROLE, grant_append_only_access
from settlement.storage.audit import COLLECTION, AuditStore

UNAUTHORIZED = 13  # MongoDB's error code for "not authorized"
PASSWORD = "writer-test"


@pytest.fixture
def admin() -> Iterator[MongoClient[dict[str, Any]]]:
    url = os.environ.get("MONGO_URL")
    if not url:
        pytest.skip("MONGO_URL not set; start MongoDB with docker compose to run this")
    client: MongoClient[dict[str, Any]] = MongoClient(url, tz_aware=True)
    yield client
    client.close()


@pytest.fixture
def database(admin: MongoClient[dict[str, Any]]) -> Iterator[Database[dict[str, Any]]]:
    """A throwaway database with the role and user set up; all removed afterwards."""
    name = f"test_access_{uuid.uuid4().hex[:8]}"
    db = admin[name]
    grant_append_only_access(db, "audit_writer", PASSWORD)
    yield db
    db.command("dropAllUsersFromDatabase")
    db.command("dropAllRolesFromDatabase")
    admin.drop_database(name)


@pytest.fixture
def writer(database: Database[dict[str, Any]]) -> Iterator[Collection[dict[str, Any]]]:
    """The audit collection, as seen by the append-only user."""
    ((host, port),) = parse_uri(os.environ["MONGO_URL"])["nodelist"]
    client: MongoClient[dict[str, Any]] = MongoClient(
        host=host,
        port=port,
        username="audit_writer",
        password=PASSWORD,
        authSource=database.name,
        tz_aware=True,
    )
    yield client[database.name][COLLECTION]
    client.close()


def event(event_id: str) -> AuditEvent:
    return AuditEvent(
        event_id=event_id,
        entity_type="Trade",
        entity_id="T-1",
        event_type="STATUS_CHANGED",
        actor="system",
        occurred_at=datetime(2026, 10, 6, 9, 0, tzinfo=UTC),
        before="BOOKED",
        after="ALLOCATED",
    )


def assert_refused(error: pytest.ExceptionInfo[OperationFailure]) -> None:
    assert error.value.code == UNAUTHORIZED
    assert "not authorized" in str(error.value)


# What the append-only user can do


def test_writer_can_add_and_read_events(writer: Collection[dict[str, Any]]) -> None:
    store = AuditStore(writer)

    store.append(event("E-1"))
    store.append(event("E-2"))

    assert [e.event_id for e in store.events_for("Trade", "T-1")] == ["E-1", "E-2"]


# What it can't: MongoDB itself refuses, whatever code asks


def test_writer_cannot_update_an_event(writer: Collection[dict[str, Any]]) -> None:
    stored = AuditStore(writer).append(event("E-1"))

    with pytest.raises(OperationFailure) as error:
        writer.update_one({"_id": "E-1"}, {"$set": {"actor": "someone-else"}})

    assert_refused(error)
    assert AuditStore(writer).events_for("Trade", "T-1") == [stored]


def test_writer_cannot_replace_an_event(writer: Collection[dict[str, Any]]) -> None:
    AuditStore(writer).append(event("E-1"))

    with pytest.raises(OperationFailure) as error:
        writer.replace_one({"_id": "E-1"}, {"actor": "someone-else"})

    assert_refused(error)


def test_writer_cannot_delete_an_event(writer: Collection[dict[str, Any]]) -> None:
    AuditStore(writer).append(event("E-1"))

    with pytest.raises(OperationFailure) as error:
        writer.delete_one({"_id": "E-1"})

    assert_refused(error)
    assert len(AuditStore(writer).events_for("Trade", "T-1")) == 1


def test_writer_cannot_drop_the_whole_log(writer: Collection[dict[str, Any]]) -> None:
    AuditStore(writer).append(event("E-1"))

    with pytest.raises(OperationFailure) as error:
        writer.drop()

    assert_refused(error)


def test_writer_cannot_skip_the_validator(writer: Collection[dict[str, Any]]) -> None:
    with pytest.raises(OperationFailure) as error:
        writer.insert_one({"_id": "JUNK"}, bypass_document_validation=True)

    assert_refused(error)
    assert writer.find_one({"_id": "JUNK"}) is None


def test_writer_cannot_give_itself_more_powers(writer: Collection[dict[str, Any]]) -> None:
    with pytest.raises(OperationFailure) as error:
        writer.database.command("createUser", "sneaky", pwd="x", roles=["readWrite"])

    assert_refused(error)


def test_writer_cannot_touch_other_collections(writer: Collection[dict[str, Any]]) -> None:
    """The role covers the audit collection only."""
    with pytest.raises(OperationFailure) as error:
        writer.database["trades"].insert_one({"_id": "T-1"})

    assert_refused(error)


# Setting it up


def test_setup_is_safe_to_run_twice(database: Database[dict[str, Any]]) -> None:
    grant_append_only_access(database, "audit_writer", PASSWORD)  # second time: no error

    (role,) = database.command("rolesInfo", ROLE, showPrivileges=True)["roles"]
    (privilege,) = role["privileges"]
    assert sorted(privilege["actions"]) == ["find", "insert"]
    assert privilege["resource"] == {"db": database.name, "collection": COLLECTION}


def test_user_has_only_the_append_only_role(database: Database[dict[str, Any]]) -> None:
    (user,) = database.command("usersInfo", "audit_writer")["users"]

    assert user["roles"] == [{"role": ROLE, "db": database.name}]

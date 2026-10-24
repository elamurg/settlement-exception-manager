"""Database-level protection for the audit store (SE-15b).

Creates a MongoDB role that may only add and read audit events, and a user with just that
role. The app connects as that user, so MongoDB itself refuses any update or delete.
"""

from typing import Any

from pymongo.database import Database

from settlement.storage.audit import COLLECTION

ROLE = "audit_appender"
ALLOWED_ACTIONS = ("insert", "find")  # what the role may do: add events and read them, nothing else


def grant_append_only_access(
    database: Database[dict[str, Any]], username: str, password: str
) -> None:
    """Create the append-only role, and a user who has only that role.

    Run once, connected as an admin. Safe to run again: anything that exists is left alone.
    """
    if not database.command("rolesInfo", ROLE)["roles"]:
        database.command(
            "createRole",
            ROLE,
            privileges=[
                {
                    "resource": {"db": database.name, "collection": COLLECTION},
                    "actions": list(ALLOWED_ACTIONS),
                }
            ],
            roles=[],
        )
    if not database.command("usersInfo", username)["users"]:
        database.command(
            "createUser",
            username,
            pwd=password,
            roles=[{"role": ROLE, "db": database.name}],
        )

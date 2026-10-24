"""Maker-checker controls on resolutions.
The person who proposes a fix (the maker) can't be the one who approves it (the checker).
Whoever amends a proposal becomes its maker, so someone else must approve the amended
version."""

from dataclasses import replace
from enum import StrEnum

from settlement.domain.errors import ControlViolation
from settlement.domain.models import (
    Resolution,
    ResolutionAction,
    ResolutionStatus,
    require_text,
)


class ReasonCode(StrEnum):
    """Why a person overrode the system's proposal. A fixed list, so overrides can be audited.

    Deliberately no OTHER: a missing reason is added to this list through review.
    """

    BOOKING_ERROR = "BOOKING_ERROR"
    COUNTERPARTY_CONFIRMED = "COUNTERPARTY_CONFIRMED"
    CUSTODIAN_ERROR = "CUSTODIAN_ERROR"
    STATIC_DATA_CORRECTED = "STATIC_DATA_CORRECTED"
    WRONG_ACTION = "WRONG_ACTION"


_FINAL = frozenset({ResolutionStatus.APPROVED, ResolutionStatus.REJECTED})


def approve(resolution: Resolution, *, approver: str) -> Resolution:
    """Approve a proposal. The maker can't approve their own (maker-checker)."""
    require_text("approver", approver)
    _require_open(resolution)
    if _same_person(approver, resolution.proposed_by):
        raise ControlViolation(
            f"{approver!r} proposed resolution {resolution.resolution_id} "
            "and can't approve it (maker-checker)."
        )
    return replace(resolution, status=ResolutionStatus.APPROVED, approved_by=approver)


def reject(resolution: Resolution, *, checker: str, reason_code: ReasonCode) -> Resolution:
    """Reject a proposal, with a reason from the fixed list. The maker can't reject their own."""
    require_text("checker", checker)
    _require_open(resolution)
    _require_reason(reason_code, "A rejection")
    if _same_person(checker, resolution.proposed_by):
        raise ControlViolation(
            f"{checker!r} proposed resolution {resolution.resolution_id} "
            "and can't reject it; withdraw it instead."
        )
    return replace(
        resolution,
        status=ResolutionStatus.REJECTED,
        approved_by=checker,
        reason_code=reason_code,
    )


def amend(
    resolution: Resolution,
    *,
    amended_by: str,
    action: ResolutionAction,
    rationale: str,
    reason_code: ReasonCode,
) -> Resolution:
    """Change a proposal, with a reason from the fixed list.
    The amender becomes the maker (`proposed_by`), so approve() will refuse them: someone
    else must approve the amended version.
    """
    require_text("amended_by", amended_by)
    _require_open(resolution)
    _require_reason(reason_code, "An amendment")
    return replace(
        resolution,
        action=action,
        rationale=rationale,
        status=ResolutionStatus.AMENDED,
        proposed_by=amended_by,
        reason_code=reason_code,
    )


def _same_person(a: str, b: str) -> bool:
    """Same user, ignoring case and surrounding spaces, so "Ela" and " ela" can't dodge a rule."""
    return a.strip().casefold() == b.strip().casefold()


def _require_open(resolution: Resolution) -> None:
    """APPROVED and REJECTED are final: a new proposal is needed instead."""
    if resolution.status in _FINAL:
        raise ControlViolation(
            f"Resolution {resolution.resolution_id} is already {resolution.status.value}; "
            "decisions are final."
        )


def _require_reason(reason_code: object, what: str) -> None:
    """An override needs a ReasonCode: free text, or a string spelled like one, isn't enough."""
    if not isinstance(reason_code, ReasonCode):
        raise ControlViolation(f"{what} needs a reason code from the fixed list.")

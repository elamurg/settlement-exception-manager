"""Tests for settlement.domain.controls: maker-checker on resolutions."""

from collections.abc import Callable
from dataclasses import replace
from itertools import product

import pytest

from settlement.domain.controls import ReasonCode, amend, approve, reject
from settlement.domain.errors import ControlViolation, InvalidModel
from settlement.domain.models import Resolution, ResolutionAction, ResolutionStatus

# A fresh proposal, made by "worker1".
PROPOSED = Resolution(
    resolution_id="R1",
    break_id="B1",
    action=ResolutionAction.REINSTRUCT,
    rationale="Re-send the instruction with the corrected quantity.",
    proposed_by="worker1",
)

OPEN = [ResolutionStatus.PROPOSED, ResolutionStatus.AMENDED]
FINAL = [ResolutionStatus.APPROVED, ResolutionStatus.REJECTED]


def in_status(status: ResolutionStatus, maker: str = "worker1") -> Resolution:
    """PROPOSED, moved to another status, with the given maker."""
    return replace(PROPOSED, status=status, proposed_by=maker)


def amend_by(resolution: Resolution, person: str) -> Resolution:
    """A valid amendment by `person`: escalate it, because the proposed action is wrong."""
    return amend(
        resolution,
        amended_by=person,
        action=ResolutionAction.ESCALATE,
        rationale="Needs a human to decide.",
        reason_code=ReasonCode.WRONG_ACTION,
    )


# Each action, done by "sam" (never the maker), with valid arguments.
ACTIONS: dict[str, Callable[[Resolution], Resolution]] = {
    "approve": lambda r: approve(r, approver="sam"),
    "reject": lambda r: reject(r, checker="sam", reason_code=ReasonCode.WRONG_ACTION),
    "amend": lambda r: amend_by(r, "sam"),
}


def test_self_approval_raises() -> None:
    with pytest.raises(ControlViolation):
        approve(PROPOSED, approver="worker1")


@pytest.mark.parametrize("approver", ["Worker1", " worker1", "worker1 ", "WORKER1"])
def test_self_approval_ignores_case_and_spaces(approver: str) -> None:
    # Every spelling of the maker's name must still be refused.
    with pytest.raises(ControlViolation):
        approve(PROPOSED, approver=approver)


def test_someone_else_can_approve() -> None:
    approved = approve(PROPOSED, approver="tina")

    assert approved.status == ResolutionStatus.APPROVED
    assert approved.approved_by == "tina"
    assert approved.proposed_by == "worker1"  # the maker doesn't change
    assert PROPOSED.status == ResolutionStatus.PROPOSED  # the original is untouched


def test_a_name_with_a_different_inner_space_is_a_different_person() -> None:
    # Only spaces around a name are ignored: "worker 1" is someone else, not "worker1".
    assert approve(PROPOSED, approver="worker 1").approved_by == "worker 1"


def test_reject_records_checker_and_reason() -> None:
    rejected = reject(PROPOSED, checker="tina", reason_code=ReasonCode.BOOKING_ERROR)

    assert rejected.status == ResolutionStatus.REJECTED
    assert rejected.approved_by == "tina"
    assert rejected.proposed_by == "worker1"
    assert rejected.reason_code == ReasonCode.BOOKING_ERROR


@pytest.mark.parametrize(
    "reason_code",
    [
        pytest.param(None, id="missing"),
        pytest.param("WRONG_ACTION", id="string_spelled_like_a_code"),
        pytest.param("felt wrong", id="free_text"),
    ],
)
def test_reject_needs_a_reason_code_from_the_list(reason_code: object) -> None:
    with pytest.raises(ControlViolation):
        reject(PROPOSED, checker="tina", reason_code=reason_code)  # type: ignore[arg-type]


def test_maker_cannot_reject_their_own() -> None:
    with pytest.raises(ControlViolation):
        reject(PROPOSED, checker="worker1", reason_code=ReasonCode.WRONG_ACTION)


def test_amend_makes_the_amender_the_maker() -> None:
    amended = amend(
        PROPOSED,
        amended_by="tina",
        action=ResolutionAction.AMEND_SSI,
        rationale="The SSI on file is stale.",
        reason_code=ReasonCode.STATIC_DATA_CORRECTED,
    )

    assert amended.status == ResolutionStatus.AMENDED
    assert amended.proposed_by == "tina"
    assert amended.action == ResolutionAction.AMEND_SSI
    assert amended.rationale == "The SSI on file is stale."
    assert amended.reason_code == ReasonCode.STATIC_DATA_CORRECTED


def test_amend_needs_a_reason_code_from_the_list() -> None:
    with pytest.raises(ControlViolation):
        amend(
            PROPOSED,
            amended_by="tina",
            action=ResolutionAction.AMEND_SSI,
            rationale="The SSI on file is stale.",
            reason_code="STATIC_DATA_CORRECTED",  # type: ignore[arg-type]
        )


def test_amender_cannot_approve_their_amendment() -> None:
    amended = amend_by(PROPOSED, "tina")

    with pytest.raises(ControlViolation):
        approve(amended, approver="tina")


def test_someone_else_can_approve_an_amendment() -> None:
    # The original maker can now be the checker: two people have still seen it.
    amended = amend_by(PROPOSED, "tina")

    approved = approve(amended, approver="worker1")

    assert approved.status == ResolutionStatus.APPROVED
    assert (approved.proposed_by, approved.approved_by) == ("tina", "worker1")


def test_maker_amending_their_own_proposal_still_cannot_approve() -> None:
    amended = amend_by(PROPOSED, "worker1")

    with pytest.raises(ControlViolation):
        approve(amended, approver="worker1")


def test_every_status_is_either_open_or_final() -> None:
    # If someone adds a status, this fails until it's put in OPEN or FINAL.
    assert set(ResolutionStatus) == set(OPEN) | set(FINAL)


@pytest.mark.parametrize(("action", "status"), list(product(ACTIONS, OPEN)))
def test_every_action_works_on_an_open_resolution(action: str, status: ResolutionStatus) -> None:
    expected = {
        "approve": ResolutionStatus.APPROVED,
        "reject": ResolutionStatus.REJECTED,
        "amend": ResolutionStatus.AMENDED,
    }

    assert ACTIONS[action](in_status(status)).status == expected[action]


@pytest.mark.parametrize(("action", "status"), list(product(ACTIONS, FINAL)))
def test_no_action_on_a_final_resolution(action: str, status: ResolutionStatus) -> None:
    with pytest.raises(ControlViolation):
        ACTIONS[action](in_status(status))


def test_reason_codes_are_spelled_exactly() -> None:
    # Catches typos like "BOOKING_ERR0R" (zero instead of O) in a name or a value.
    assert {code.name: code.value for code in ReasonCode} == {
        "BOOKING_ERROR": "BOOKING_ERROR",
        "COUNTERPARTY_CONFIRMED": "COUNTERPARTY_CONFIRMED",
        "CUSTODIAN_ERROR": "CUSTODIAN_ERROR",
        "STATIC_DATA_CORRECTED": "STATIC_DATA_CORRECTED",
        "WRONG_ACTION": "WRONG_ACTION",
    }


BLANK_PERSON: dict[str, Callable[[], Resolution]] = {
    "approve": lambda: approve(PROPOSED, approver=" "),
    "reject": lambda: reject(PROPOSED, checker=" ", reason_code=ReasonCode.WRONG_ACTION),
    "amend": lambda: amend_by(PROPOSED, " "),
}


@pytest.mark.parametrize("action", list(BLANK_PERSON))
def test_blank_person_is_rejected(action: str) -> None:
    with pytest.raises(InvalidModel):
        BLANK_PERSON[action]()

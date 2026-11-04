"""Tests for three-way attribution (SE-12a)."""

from datetime import UTC, datetime

import pytest

from settlement.domain.attribution import (
    REASON_BREAK_TYPES,
    CsdStatus,
    Leg,
    break_type_for_reason,
    latest_statuses,
    locate,
)
from settlement.domain.errors import InvalidModel
from settlement.domain.models import BreakType

MATCHED = CsdStatus("INSTR-1", matched=True, reason=None)
NOT_MATCHED = CsdStatus("INSTR-1", matched=False, reason="DQUA")


# locate: the table from the design, one row per test case


@pytest.mark.parametrize(
    ("book_custodian_break", "status", "expected"),
    [
        pytest.param(True, MATCHED, Leg.INTERNAL, id="only_leg_1_broken"),
        pytest.param(False, NOT_MATCHED, Leg.COUNTERPARTY, id="only_leg_2_broken"),
        pytest.param(True, NOT_MATCHED, Leg.BOTH, id="both_legs_broken"),
        pytest.param(False, MATCHED, None, id="nothing_broken"),
        pytest.param(True, None, Leg.UNKNOWN, id="break_but_no_status_yet"),
        pytest.param(False, None, Leg.UNKNOWN, id="no_break_but_no_status_yet"),
    ],
)
def test_locate(book_custodian_break: bool, status: CsdStatus | None, expected: Leg | None) -> None:
    assert locate(book_custodian_break, status) == expected


def test_ticket_done_when() -> None:
    """SE-12: a break between book and custodian, absent between custodian and CSD,
    is put on our side."""
    assert locate(book_custodian_break=True, status=MATCHED) == Leg.INTERNAL


# CsdStatus validation


def test_valid_statuses() -> None:
    assert MATCHED.reason is None
    assert NOT_MATCHED.reason == "DQUA"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        pytest.param(
            {"instruction_reference": " ", "matched": True, "reason": None},
            "instruction_reference",
            id="blank_reference",
        ),
        pytest.param(
            {"instruction_reference": "INSTR-1", "matched": "yes", "reason": None},
            "True or False",
            id="matched_not_a_bool",
        ),
        pytest.param(
            {"instruction_reference": "INSTR-1", "matched": True, "reason": "DQUA"},
            "can't have a reason",
            id="matched_with_reason",
        ),
        pytest.param(
            {"instruction_reference": "INSTR-1", "matched": False, "reason": None},
            "needs a reason",
            id="unmatched_without_reason",
        ),
        pytest.param(
            {"instruction_reference": "INSTR-1", "matched": False, "reason": "dqua"},
            "4-letter code",
            id="reason_lowercase",
        ),
        pytest.param(
            {"instruction_reference": "INSTR-1", "matched": False, "reason": "QUANTITY"},
            "4-letter code",
            id="reason_too_long",
        ),
        pytest.param(
            {"instruction_reference": "INSTR-1", "matched": False, "reason": "DQU"},
            "4-letter code",
            id="reason_one_too_short",
        ),
        pytest.param(
            {"instruction_reference": "INSTR-1", "matched": False, "reason": "DQUAX"},
            "4-letter code",
            id="reason_one_too_long",
        ),
    ],
)
def test_invalid_status(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(InvalidModel, match=message):
        CsdStatus(**kwargs)  # type: ignore[arg-type]


# csdstatus time
def test_status_time_is_optional() -> None:
    assert MATCHED.at is None


def test_status_keeps_an_aware_time() -> None:
    at = datetime(2026, 10, 5, 9, 30, tzinfo=UTC)
    assert CsdStatus("INSTR-1", matched=True, reason=None, at=at).at == at


def test_status_time_without_a_timezone_is_rejected() -> None:
    with pytest.raises(InvalidModel, match="at must be a timezone-aware datetime"):
        CsdStatus("INSTR-1", matched=True, reason=None, at=datetime(2026, 10, 5, 9, 30))


# Reason codes


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("CMIS", BreakType.UNMATCHED_INSTRUCTION),
        ("DQUA", BreakType.QUANTITY_BREAK),
        ("DDAT", BreakType.DATE_MISMATCH),
        ("DMON", BreakType.PRICE_BREAK),
        ("DEPT", BreakType.SSI_MISMATCH),
    ],
)
def test_reason_codes_map_to_break_types(reason: str, expected: BreakType) -> None:
    assert break_type_for_reason(reason) == expected


def test_reason_table_cannot_be_changed() -> None:
    """Everything trusts this table, so an accidental edit must fail loudly."""
    with pytest.raises(TypeError):
        REASON_BREAK_TYPES["XXXX"] = BreakType.STATIC_DATA  # type: ignore[index]


def test_unknown_reason_code_is_not_guessed() -> None:
    assert break_type_for_reason("ZZZZ") is None


# latest statuses: the CSD sends a new MT548 each time a status changes during the day
def at(hour: int) -> datetime:
    return datetime(2026, 10, 5, hour, tzinfo=UTC)


def status(matched: bool, hour: int | None, reference: str = "T1") -> CsdStatus:
    reason = None if matched else "DQUA"
    return CsdStatus(reference, matched, reason, at(hour) if hour is not None else None)


def test_one_status_is_the_answer() -> None:
    assert latest_statuses([status(True, 9)]) == ({"T1": status(True, 9)}, ())


def test_counterparty_fixes_their_side_by_lunchtime() -> None:
    # NMAT in the morning
    # MACH after the counterparty corrected their instruction.
    morning, lunch = status(False, 9), status(True, 12)

    assert latest_statuses([morning, lunch]) == ({"T1": lunch}, ())


def test_order_in_the_file_does_not_matter() -> None:
    morning, lunch = status(False, 9), status(True, 12)

    assert latest_statuses([lunch, morning]) == ({"T1": lunch}, ())


def test_a_later_nmat_wins_over_an_earlier_mach() -> None:
    morning, afternoon = status(True, 9), status(False, 15)

    assert latest_statuses([morning, afternoon]) == ({"T1": afternoon}, ())


def test_the_same_message_sent_twice_is_not_a_conflict() -> None:
    assert latest_statuses([status(True, 9), status(True, 9)]) == ({"T1": status(True, 9)}, ())


def test_statuses_that_agree_are_not_a_conflict() -> None:
    chosen, conflicts = latest_statuses([status(True, 9), status(True, None)])

    assert chosen["T1"].matched is True
    assert conflicts == ()


def test_different_answers_at_the_same_time_are_a_conflict() -> None:
    mach, nmat = status(True, 9), status(False, 9)

    assert latest_statuses([mach, nmat]) == ({}, (mach, nmat))


def test_disagreeing_statuses_without_a_time_are_a_conflict() -> None:
    mach, nmat = status(True, None), status(False, None)

    assert latest_statuses([mach, nmat]) == ({}, (mach, nmat))


def test_one_conflict_does_not_affect_other_instructions() -> None:
    clash = [status(True, 9, "T1"), status(False, 9, "T1")]
    fine = status(True, 9, "T2")

    chosen, conflicts = latest_statuses([*clash, fine])

    assert chosen == {"T2": fine}
    assert conflicts == tuple(clash)

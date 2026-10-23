"""Tests for three-way attribution (SE-12a)."""

import pytest

from settlement.domain.attribution import (
    CsdStatus,
    Leg,
    break_type_for_reason,
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
    ],
)
def test_invalid_status(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(InvalidModel, match=message):
        CsdStatus(**kwargs)  # type: ignore[arg-type]


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


def test_unknown_reason_code_is_not_guessed() -> None:
    assert break_type_for_reason("ZZZZ") is None

"""Tests for the CSD's view (SE-12b): generated MT548 statuses and reading them back."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from settlement.domain.attribution import REASON_BREAK_TYPES, CsdStatus, Leg, locate
from settlement.domain.models import BreakType
from settlement.generation import files
from settlement.generation.__main__ import main
from settlement.generation.generator import (
    GeneratedData,
    GenerationConfig,
    generate,
    scaled_rates,
)
from settlement.ingestion.errors import ParseError
from settlement.ingestion.statuses import load_statuses, parse_statuses

# Plenty of both kinds of break in a small run.
BUSY = GenerationConfig(
    trades=2000, seed=7, break_rates=scaled_rates(0.4), counterparty_break_rate=0.3
)


@pytest.fixture(scope="module")
def busy() -> GeneratedData:
    return generate(BUSY)


# What the generator produces


def test_every_statement_line_gets_one_status(busy: GeneratedData) -> None:
    references = [status.instruction_reference for status in busy.statuses]

    assert references == [line.reference for line in busy.statement]


def test_unmatched_instructions_get_no_status(busy: GeneratedData) -> None:
    """The custodian never received them, so there's nothing for the CSD to match."""
    with_status = {status.instruction_reference for status in busy.statuses}
    unmatched = [b for b in busy.breaks if b.break_type == BreakType.UNMATCHED_INSTRUCTION]

    assert unmatched
    assert all(item.trade_id not in with_status for item in unmatched)


def test_leg_1_breaks_match_at_the_csd(busy: GeneratedData) -> None:
    """Book and custodian disagree, but custodian and counterparty agree: our side."""
    status_for = {status.instruction_reference: status for status in busy.statuses}

    for injected in busy.breaks:
        assert injected.leg == Leg.INTERNAL
        status = status_for.get(injected.trade_id)
        if status is not None:
            assert status.matched
            assert locate(book_custodian_break=True, status=status) == Leg.INTERNAL


def test_counterparty_breaks_are_unmatched_with_their_reason(busy: GeneratedData) -> None:
    status_for = {status.instruction_reference: status for status in busy.statuses}

    assert busy.counterparty_breaks
    for item in busy.counterparty_breaks:
        status = status_for[item.trade_id]
        assert (status.matched, status.reason) == (False, item.reason)
        assert item.break_type == REASON_BREAK_TYPES[item.reason]
        assert item.leg == Leg.COUNTERPARTY
        assert locate(book_custodian_break=False, status=status) == Leg.COUNTERPARTY


def test_each_break_is_on_one_leg_only(busy: GeneratedData) -> None:
    leg_1 = {injected.trade_id for injected in busy.breaks}
    leg_2 = {item.trade_id for item in busy.counterparty_breaks}

    assert leg_1.isdisjoint(leg_2)


def test_every_unmatched_status_is_a_counterparty_break(busy: GeneratedData) -> None:
    unmatched = {s.instruction_reference for s in busy.statuses if not s.matched}

    assert unmatched == {item.trade_id for item in busy.counterparty_breaks}


def test_every_reason_code_turns_up(busy: GeneratedData) -> None:
    assert {item.reason for item in busy.counterparty_breaks} == set(REASON_BREAK_TYPES)


def test_cmis_is_the_most_common_reason(busy: GeneratedData) -> None:
    reasons = [item.reason for item in busy.counterparty_breaks]

    assert max(set(reasons), key=reasons.count) == "CMIS"


def test_counterparty_rate_of_zero_means_everything_matches() -> None:
    data = generate(replace(BUSY, counterparty_break_rate=0.0))

    assert data.counterparty_breaks == ()
    assert all(status.matched for status in data.statuses)


def test_book_and_statement_dont_depend_on_the_counterparty_rate() -> None:
    """The CSD statuses use their own random stream, so recorded baselines stay valid."""
    without = generate(replace(BUSY, counterparty_break_rate=0.0))
    with_some = generate(BUSY)

    assert (without.book, without.statement, without.breaks) == (
        with_some.book,
        with_some.statement,
        with_some.breaks,
    )


def test_same_seed_gives_the_same_statuses() -> None:
    assert generate(BUSY).statuses == generate(BUSY).statuses


@pytest.mark.parametrize("rate", [-0.1, 1.5, float("nan")])
def test_invalid_counterparty_rate_is_rejected(rate: float) -> None:
    with pytest.raises(ValueError, match="counterparty break rate"):
        replace(BUSY, counterparty_break_rate=rate)


# Written to statuses.txt and read back


def test_statuses_round_trip_through_the_file(busy: GeneratedData, tmp_path: Path) -> None:
    files.write_all(busy, tmp_path)

    read_back = load_statuses(tmp_path / files.STATUSES_FILE)

    as_domain = tuple(
        CsdStatus(message.related_reference, message.matched, message.reason)
        for message in read_back
    )
    assert as_domain == busy.statuses
    assert [message.reference for message in read_back[:2]] == ["STAT-000001", "STAT-000002"]


def test_manifest_records_legs_and_counterparty_breaks(busy: GeneratedData, tmp_path: Path) -> None:
    files.write_all(busy, tmp_path)
    manifest = json.loads((tmp_path / files.MANIFEST_FILE).read_text())

    assert {item["leg"] for item in manifest["breaks"]} == {"INTERNAL"}
    assert len(manifest["counterparty_breaks"]) == manifest["counts"]["counterparty_breaks"]
    assert {item["leg"] for item in manifest["counterparty_breaks"]} == {"COUNTERPARTY"}
    assert manifest["counts"]["statuses"] == len(busy.statuses)


def test_command_line_writes_statuses(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(
        [
            "--trades",
            "200",
            "--seed",
            "1",
            "--out",
            str(tmp_path),
            "--counterparty-break-rate",
            "0.2",
        ]
    )

    assert (tmp_path / files.STATUSES_FILE).exists()
    assert "counterparty breaks" in capsys.readouterr().out


# Reading a statuses file: errors name the line in the whole file

MATCHED = """\
:16R:GENL
:20C::SEME//STAT-1
:23G:INST
:16R:LINK
:20C::RELA//T-1
:16S:LINK
:16R:STAT
:25D::MTCH//MACH
:16S:STAT
:16S:GENL
"""


def test_two_messages_are_read() -> None:
    first, second = parse_statuses(MATCHED + "-\n" + MATCHED.replace("T-1", "T-2") + "-\n")

    assert (first.related_reference, second.related_reference) == ("T-1", "T-2")


def test_an_error_in_the_second_message_names_the_line_in_the_file() -> None:
    broken = MATCHED.replace("MTCH//MACH", "MTCH//MAYB")  # line 8 of the message
    text = MATCHED + "-\n" + broken + "-\n"  # 10 lines + separator, so line 19 of the file

    with pytest.raises(ParseError) as caught:
        parse_statuses(text)

    assert caught.value.line == 19
    assert str(caught.value).startswith("statuses.txt line 19")
    assert "must be MACH or NMAT" in str(caught.value)


def test_last_message_must_be_ended() -> None:
    with pytest.raises(ParseError, match="line 12: last message isn't ended"):
        parse_statuses(MATCHED + "-\n" + MATCHED)


def test_empty_message_between_separators() -> None:
    with pytest.raises(ParseError, match="line 12: empty message"):
        parse_statuses(MATCHED + "-\n-\n")


def test_empty_file_has_no_statuses() -> None:
    assert parse_statuses("") == ()

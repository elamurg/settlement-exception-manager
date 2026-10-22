"""Tests for settlement.reconciliation.tolerances (YAML to ToleranceSet)."""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from settlement.domain.tolerance import ToleranceRule, ToleranceSet
from settlement.reconciliation.errors import ToleranceError
from settlement.reconciliation.tolerances import load_tolerance_set, parse_tolerance_set

CONFIG_DIR = Path(__file__).parent.parent / "config" / "tolerances"

HEADER = 'version: "v1"\neffective_from: "2026-10-01"\n'


def rule_yaml(
    currency: str | None = '"EUR"',
    instrument_type: str | None = '"*"',
    absolute: str | None = '"5.00"',
    percentage: str | None = '"0.0001"',
    extra: str | None = None,
) -> str:
    """One rule as YAML. Pass None to leave a key out; values are written exactly as given."""
    fields = {
        "currency": currency,
        "instrument_type": instrument_type,
        "absolute": absolute,
        "percentage": percentage,
    }
    lines = [f"{key}: {value}" for key, value in fields.items() if value is not None]
    if extra is not None:
        lines.append(extra)
    return "  - " + "\n    ".join(lines) + "\n"


FALLBACK_YAML = rule_yaml('"*"', '"*"', '"1.00"', '"0"')


def document(*rules: str, header: str = HEADER) -> str:
    """A whole tolerance file: header, then the given rules (default: EUR plus fallback)."""
    return header + "rules:\n" + "".join(rules or (rule_yaml(), FALLBACK_YAML))


def test_valid_document_parses_into_domain_objects() -> None:
    assert parse_tolerance_set(document()) == ToleranceSet(
        version="v1",
        effective_from=date(2026, 10, 1),
        rules=(
            ToleranceRule("EUR", "*", Decimal("5.00"), Decimal("0.0001")),
            ToleranceRule("*", "*", Decimal("1.00"), Decimal("0")),
        ),
    )


def test_spaces_around_values_are_stripped() -> None:
    parsed = parse_tolerance_set(
        document(header='version: "  v1 "\neffective_from: "2026-10-01"\n')
    )
    assert parsed.version == "v1"


def test_committed_v1_file_is_valid() -> None:
    tolerances = load_tolerance_set(CONFIG_DIR / "v1.yaml")

    assert tolerances.version == "v1"
    assert tolerances.rule_for("EUR", "*").absolute == Decimal("5.00")
    assert tolerances.rule_for("JPY", "*").is_fallback


def test_missing_file_names_the_file(tmp_path: Path) -> None:
    with pytest.raises(ToleranceError, match=re.escape("v9.yaml: Cannot read file")):
        load_tolerance_set(tmp_path / "v9.yaml")


def test_non_utf8_file_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "latin1.yaml"
    path.write_bytes(document().replace('"*"', '"Müller"').encode("latin-1"))

    with pytest.raises(ToleranceError, match=re.escape("latin1.yaml: Cannot read file")):
        load_tolerance_set(path)


def test_errors_from_a_file_name_that_file(tmp_path: Path) -> None:
    path = tmp_path / "v2.yaml"
    path.write_text(document(rule_yaml(absolute="5.00"), FALLBACK_YAML), encoding="utf-8")

    with pytest.raises(ToleranceError, match=re.escape("v2.yaml: rules[1].absolute")):
        load_tolerance_set(path)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        pytest.param("version: [unclosed", "Not a valid YAML", id="broken_yaml"),
        pytest.param("", "Expected mapping", id="empty_file"),
        pytest.param("- a\n- b\n", "Expected mapping", id="list_not_mapping"),
        pytest.param(
            document(rule_yaml(extra='absolute: "500.00"'), FALLBACK_YAML),
            "Not a valid YAML: duplicate key 'absolute'",
            id="duplicate_key_in_rule",
        ),
        pytest.param(
            document() + 'version: "v2"\n',
            "Not a valid YAML: duplicate key 'version'",
            id="duplicate_top_level_key",
        ),
        pytest.param(
            'version: "v1"\nrules: []\n', "top level: missing keys: effective_from", id="no_date"
        ),
        pytest.param(
            document() + 'owner: "ops"\n', "top level: unknown keys: owner", id="unknown_top_key"
        ),
        pytest.param(HEADER + "rules: []\n", "Rules must be a non-empty list", id="empty_rules"),
        pytest.param(HEADER + 'rules: "EUR"\n', "Rules must be a non-empty list", id="rules_text"),
        pytest.param(HEADER + 'rules:\n  - "EUR"\n', "rules[1] must be a mapping", id="rule_text"),
        pytest.param(
            document(rule_yaml(percentage=None), FALLBACK_YAML),
            "rules[1]: missing keys: percentage",
            id="rule_missing_key",
        ),
        pytest.param(
            document(rule_yaml(extra='note: "x"'), FALLBACK_YAML),
            "rules[1]: unknown keys: note",
            id="rule_unknown_key",
        ),
        pytest.param(
            document(FALLBACK_YAML, rule_yaml(absolute='"five"')),
            "rules[2].absolute is not a number",
            id="second_rule_not_a_number",
        ),
        pytest.param(
            document(rule_yaml(absolute="5.00"), FALLBACK_YAML),
            "rules[1].absolute must be a quoted string",
            id="unquoted_amount",
        ),
        pytest.param(
            document(header='version: "v1"\neffective_from: 2026-10-01\n'),
            "effective_from must be a quoted string",
            id="unquoted_date",
        ),
        pytest.param(
            document(header='version: 1\neffective_from: "2026-10-01"\n'),
            "version must be a quoted string",
            id="unquoted_version",
        ),
        pytest.param(
            document(header='version: "v1"\neffective_from: "01/10/2026"\n'),
            "effective_from is not a date",
            id="wrong_date_format",
        ),
        pytest.param(
            document(rule_yaml(absolute='"-1"'), FALLBACK_YAML),
            "rules[1]: absolute must not be negative",
            id="negative_amount",
        ),
        pytest.param(
            document(rule_yaml(absolute='"NaN"'), FALLBACK_YAML),
            "rules[1]: absolute must be a finite number",
            id="nan_amount",
        ),
        pytest.param(
            document(rule_yaml(currency='"eur"'), FALLBACK_YAML),
            "rules[1]: Currency must be a 3-letter code",
            id="lowercase_currency",
        ),
        pytest.param(
            document(rule_yaml(percentage='"5"'), FALLBACK_YAML),
            "rules[1]: Percentage is a fraction",
            id="percentage_as_whole_number",
        ),
        pytest.param(document(rule_yaml()), "exactly one fallback rule", id="no_fallback"),
        pytest.param(
            document(rule_yaml(), rule_yaml(), FALLBACK_YAML),
            "Duplicate rule for EUR",
            id="duplicate_rule",
        ),
    ],
)
def test_invalid_document_raises(text: str, message: str) -> None:
    with pytest.raises(ToleranceError, match=re.escape(f"tolerances.yaml: {message}")):
        parse_tolerance_set(text)

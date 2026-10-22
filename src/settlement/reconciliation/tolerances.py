"""Load a version tolerance set from YAML into ToleranceSet"""

from collections.abc import Hashable
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from settlement.domain.errors import InvalidModel
from settlement.domain.tolerance import ToleranceRule, ToleranceSet
from settlement.reconciliation.errors import ToleranceError

_TOP_LEVEL_KEYS = {"version", "effective_from", "rules"}
_RULE_KEYS = {"currency", "instrument_type", "absolute", "percentage"}


class _UniqueKeyLoader(yaml.SafeLoader):
    """yaml.safe_load, except a key that appears twice in one mapping is an error."""


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    seen: set[Hashable] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node)
        if isinstance(key, Hashable) and key in seen:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def load_tolerance_set(path: Path) -> ToleranceSet:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        # OSError catches missing files and permission problems
        # UnicodeDecodeError covers files that arent valid UTF-8
        raise ToleranceError(path.name, f"Cannot read file: {e}") from None
    return parse_tolerance_set(text, source=path.name)


def parse_tolerance_set(text: str, source: str = "tolerances.yaml") -> ToleranceSet:
    try:
        # safeload only ever produces plain data
        data = yaml.safe_load(text)
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as e:
        raise ToleranceError(source, f"Not a valid YAML: {e}") from None

    if not isinstance(data, dict):
        raise ToleranceError(source, "Expected mapping with version, effective_from, rules")
    _require_exact_keys(data, _TOP_LEVEL_KEYS, source, "top level")

    raw_rules = data["rules"]
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ToleranceError(source, "Rules must be a non-empty list.")

    rules = tuple(
        _parse_rule(raw, source, where=f"rules[{number}]")
        for number, raw in enumerate(raw_rules, start=1)
    )

    try:
        return ToleranceSet(
            version=_text(data["version"], source, "version"),
            effective_from=_date(data["effective_from"], source, "effective_from"),
            rules=rules,
        )

    except InvalidModel as e:
        raise ToleranceError(source, str(e)) from None


def _parse_rule(raw: Any, source: str, where: str) -> ToleranceRule:
    if not isinstance(raw, dict):
        raise ToleranceError(source, f"{where} must be a mapping")
    _require_exact_keys(raw, _RULE_KEYS, source, where)
    try:
        return ToleranceRule(
            currency=_text(raw["currency"], source, f"{where}.currency"),
            instrument_type=_text(raw["instrument_type"], source, f"{where}.instrument_type"),
            absolute=_decimal(raw["absolute"], source, f"{where}.absolute"),
            percentage=_decimal(raw["percentage"], source, f"{where}.percentage"),
        )
    except InvalidModel as e:
        raise ToleranceError(source, f"{where}: {e}") from None


def _require_exact_keys(data: dict[Any, Any], expected: set[str], source: str, where: str) -> None:
    missing = sorted(expected - data.keys())
    unknown = sorted(str(key) for key in data.keys() - expected)
    if missing:
        raise ToleranceError(source, f"{where}: missing keys: {', '.join(missing)}")
    if unknown:
        raise ToleranceError(source, f"{where}: unknown keys: {', '.join(unknown)}")


def _text(value: Any, source: str, where: str) -> str:
    if not isinstance(value, str):
        raise ToleranceError(source, f"{where} must be a quoted string, got {value!r}")
    return value.strip()


def _decimal(value: Any, source: str, where: str) -> Decimal:
    raw = _text(value, source, where)
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise ToleranceError(source, f"{where} is not a number: {raw!r}") from None


def _date(value: Any, source: str, where: str) -> date:
    raw = _text(value, source, where)
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ToleranceError(source, f"{where} is not a date: {raw!r}") from None

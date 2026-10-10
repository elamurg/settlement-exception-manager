"""Shared CSV reading for the book and statement parsers.

Strict on purpose: a missing, unknown or duplicated column, a row with the wrong number
of fields, or a value that doesn't parse raises ParseError naming the line and field.
Nothing is skipped silently. Blank lines carry no data and are the only thing ignored.

Values are stripped of surrounding spaces, and codes (identifiers, currencies, enums)
are upper-cased, because the domain types are strict and expect normalised input.
"""

import csv
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from settlement.domain.errors import InvalidIdentifier, InvalidModel
from settlement.domain.identifiers import ISIN, LEI
from settlement.ingestion.errors import ParseError


@dataclass(frozen=True, slots=True)
class Row:
    source: str
    line: int
    values: Mapping[str, str]

    def error(self, message: str, field: str | None = None) -> ParseError:
        return ParseError(self.source, self.line, message, field)

    def text(self, field: str) -> str:
        value = self.values[field].strip()
        if not value:
            raise self.error("is empty", field)
        return value

    def optional_text(self, field: str) -> str | None:
        """None when the column is absent or the value is empty."""
        value = self.values.get(field, "").strip()
        return value or None

    def code(self, field: str) -> str:
        return self.text(field).upper()

    def decimal(self, field: str) -> Decimal:
        raw = self.text(field)
        try:
            value = Decimal(raw)
        except InvalidOperation:
            raise self.error(f"is not a number: {raw!r}", field) from None
        if not value.is_finite():
            raise self.error(f"is not a finite number: {raw!r}", field)
        return value

    def date(self, field: str) -> date:
        raw = self.text(field)
        try:
            return date.fromisoformat(raw)
        except ValueError:
            raise self.error(f"is not a date (YYYY-MM-DD): {raw!r}", field) from None

    def choice[E: StrEnum](self, field: str, enum: type[E]) -> E:
        raw = self.code(field)
        try:
            return enum(raw)
        except ValueError:
            allowed = ", ".join(member.value for member in enum)
            raise self.error(f"must be one of {allowed}; got {raw!r}", field) from None

    def isin(self, field: str) -> ISIN:
        try:
            return ISIN(self.code(field))
        except InvalidIdentifier as error:
            raise self.error(str(error), field) from None

    def lei(self, field: str) -> LEI:
        try:
            return LEI(self.code(field))
        except InvalidIdentifier as error:
            raise self.error(str(error), field) from None

    def build[T](self, make: Callable[[], T]) -> T:
        """Run a model constructor, turning its validation error into a ParseError."""
        try:
            return make()
        except InvalidModel as error:
            raise self.error(str(error)) from None


def read_rows(
    lines: Iterable[str],
    source: str,
    required: Iterable[str],
    optional: Iterable[str] = (),
) -> Iterator[Row]:
    reader = csv.reader(lines)
    header = next(reader, None)
    if header is None:
        raise ParseError(source, 1, "file is empty; expected a header row")

    header = [name.strip() for name in header]
    required, optional = list(required), list(optional)
    duplicated = sorted({name for name in header if header.count(name) > 1})
    missing = [name for name in required if name not in header]
    unknown = [name for name in header if name not in required and name not in optional]
    if duplicated:
        raise ParseError(source, 1, f"duplicated columns: {', '.join(duplicated)}")
    if missing:
        raise ParseError(source, 1, f"missing columns: {', '.join(missing)}")
    if unknown:
        raise ParseError(source, 1, f"unknown columns: {', '.join(unknown)}")

    for fields in reader:
        if not fields:
            continue
        if len(fields) != len(header):
            raise ParseError(
                source, reader.line_num, f"expected {len(header)} fields, got {len(fields)}"
            )
        yield Row(source, reader.line_num, dict(zip(header, fields, strict=True)))


def require_unique(row: Row, field: str, value: str, seen: dict[str, int]) -> None:
    """Fail on an id that has already appeared, naming the line it first appeared on."""
    if value in seen:
        raise row.error(f"{value!r} already appears on line {seen[value]}", field)
    seen[value] = row.line

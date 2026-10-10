"""Errors raised while reading input files."""

from settlement.domain.errors import DomainError


class ParseError(DomainError):
    """A file couldn't be read. Says which file, which line and, where known, which field."""

    def __init__(self, source: str, line: int, message: str, field: str | None = None) -> None:
        self.source = source
        self.line = line
        self.field = field
        where = f"{source} line {line}" + (f", field {field!r}" if field else "")
        super().__init__(f"{where}: {message}")

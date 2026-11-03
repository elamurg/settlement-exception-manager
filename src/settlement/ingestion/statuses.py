"""Read a file of MT548 status messages (statuses.txt): one message after another, each
ended by a line holding only "-", as a message body ends in SWIFT.

Errors name the line in the whole file, not just within one message.
"""

from pathlib import Path

from settlement.ingestion.errors import ParseError
from settlement.ingestion.messages import StatusMessage, parse_mt548

SEPARATOR = "-"


def parse_statuses(text: str, source: str = "statuses.txt") -> tuple[StatusMessage, ...]:
    statuses: list[StatusMessage] = []
    message_lines: list[str] = []
    first_line = 1  # where the current message starts in the file
    for number, line in enumerate(text.splitlines(), start=1):
        if line.strip() != SEPARATOR:
            message_lines.append(line)
            continue
        if not "".join(message_lines).strip():
            raise ParseError(source, number, "empty message before this separator")
        statuses.append(_parse_one(message_lines, first_line, source))
        message_lines = []
        first_line = number + 1

    if "".join(message_lines).strip():
        raise ParseError(source, first_line, f"last message isn't ended by a {SEPARATOR!r} line")
    return tuple(statuses)


def load_statuses(path: Path) -> tuple[StatusMessage, ...]:
    return parse_statuses(path.read_text(), source=path.name)


def _parse_one(lines: list[str], first_line: int, source: str) -> StatusMessage:
    try:
        return parse_mt548("\n".join(lines), source)
    except ParseError as error:
        # parse_mt548 counts from the start of the message; shift to the line in the file.
        raise ParseError(source, error.line + first_line - 1, error.message, error.field) from None

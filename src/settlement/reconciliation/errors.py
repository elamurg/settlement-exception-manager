"""Errors raised while loading reconciliation configuration."""

from settlement.domain.errors import DomainError


class ToleranceError(DomainError):
    """Raised when a tolerance file couldn't be loaded. Says which file and where it is."""

    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")

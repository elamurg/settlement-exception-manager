"""Errors raised by the storage layer."""

from settlement.domain.errors import DomainError


class AuditStoreError(DomainError):
    """Raised when the audit store refuses an operation, e.g. recording an event twice."""

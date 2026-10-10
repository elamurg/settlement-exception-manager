"""Errors used in settlement.domain.models."""


class DomainError(Exception):
    """Base class for all domain errors."""


class InvalidIdentifier(DomainError):
    """Raised when an invalid identifier is used."""


class InvalidModel(DomainError):
    """Raised when an invalid model is used."""

class DomainError(Exception):
    """Base class for domain-level validation errors."""


class ProjectValidationError(DomainError):
    """Raised when a project or operation violates domain rules."""

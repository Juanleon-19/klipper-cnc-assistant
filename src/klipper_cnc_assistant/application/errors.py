class ApplicationError(Exception):
    """Base class for application service errors."""


class NotFoundError(ApplicationError):
    """Raised when a requested resource does not exist."""

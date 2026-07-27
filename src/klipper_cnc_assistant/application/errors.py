class ApplicationError(Exception):
    """Base class for application service errors."""


class NotFoundError(ApplicationError):
    """Raised when a requested resource does not exist."""


class ConflictError(ApplicationError):
    """Raised when a requested action conflicts with the current resource state."""

    def __init__(self, message: str, *, current_state: str | None = None, run_id: str | None = None, allowed_action: str | None = None) -> None:
        super().__init__(message)
        self.status_code = 409
        self.payload = {
            "current_state": current_state,
            "run_id": run_id,
            "allowed_action": allowed_action,
            "message": message,
        }

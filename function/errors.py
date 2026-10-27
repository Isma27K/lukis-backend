class DomainError(Exception):
    """An expected failure that maps straight to an HTTP error response."""

    status_code: int
    detail: str

    def __init__(self, detail: str | None = None):
        if detail is not None:
            self.detail = detail
        super().__init__(self.detail)


class NotFoundError(DomainError):
    """It doesn't exist, or the user isn't allowed to know that it does."""

    status_code = 404
    detail = "Not found"


class ForbiddenError(DomainError):
    """The user can see it but their role doesn't allow this action."""

    status_code = 403
    detail = "You don't have permission to do that"


class ConflictError(DomainError):
    """The request clashes with the current state, e.g. removing the last owner."""

    status_code = 409
    detail = "That conflicts with a change someone else made"


class InvalidFileError(DomainError):
    """An upload that isn't a supported image."""

    status_code = 422
    detail = "That file type isn't supported"


class FileTooLargeError(DomainError):
    status_code = 413
    detail = "That file is too large"

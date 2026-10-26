class NotFoundError(Exception):
    """It doesn't exist, or the user isn't allowed to know that it does."""


class ForbiddenError(Exception):
    """The user can see it but their role doesn't allow this action."""


class ConflictError(Exception):
    """A write lost too many races with other writers; the client should retry."""

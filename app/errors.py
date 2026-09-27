"""Application failures independent of HTTP response handling."""


class DuplicateEmail(Exception):
    """An account already uses the normalized email address."""


class InvalidCredentials(Exception):
    """The supplied login credentials could not be authenticated."""


class InvalidToken(Exception):
    """An access token failed signature, claim, or subject validation."""


class EventNotFound(Exception):
    """No event exists for the requested identifier."""

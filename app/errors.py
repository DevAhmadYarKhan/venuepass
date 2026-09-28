"""Application failures independent of HTTP response handling."""


class DuplicateEmail(Exception):
    """An account already uses the normalized email address."""


class InvalidCredentials(Exception):
    """The supplied login credentials could not be authenticated."""


class InvalidToken(Exception):
    """An access token failed signature, claim, or subject validation."""


class EventNotFound(Exception):
    """No event exists for the requested identifier."""


class UserNotFound(Exception):
    """No account exists for the requested normalized email."""


class VenueNotFound(Exception):
    """No venue exists for the requested identifier."""


class VenueOwnershipRequired(Exception):
    """Only the venue owner may change its seating."""


class DuplicateSeat(Exception):
    """The batch contains a repeated or already-existing seat identity."""

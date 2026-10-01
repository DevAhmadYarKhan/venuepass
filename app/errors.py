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


class VenueAccessDenied(Exception):
    """The organizer lacks the venue owner's authorization."""


class OrganizerRequired(Exception):
    """The target account does not currently have organizer permission."""


class EmptyVenue(Exception):
    """An event cannot be created without physical seats."""


class ReservationNotFound(Exception):
    """No reservation is visible to the requesting user."""


class InvalidReservationSeats(Exception):
    """One or more requested seats are not in the event's fixed membership."""


class BookingConflict(Exception):
    """The event, seats, or retry key conflict with a new booking."""


class EventOwnershipRequired(Exception):
    """Only the owning organizer may cancel an event."""


class EventCancellationConflict(Exception):
    """An event has already started and cannot be cancelled for the first time."""

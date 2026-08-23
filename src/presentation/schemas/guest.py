"""Request/response schemas for the guest (unauthenticated) conversion surface."""

from src.presentation.schemas.conversion import ConversionJobResponse


class GuestJobResponse(ConversionJobResponse):
    """A conversion job returned to a guest, carrying the minted access token.

    The token is the only authorization for the guest surface: a ``job_id``
    alone grants nothing. Guests use it to poll status, stream events, and
    download the output.
    """

    guest_token: str

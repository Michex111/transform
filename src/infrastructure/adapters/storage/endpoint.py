"""S3 endpoint helpers."""

_SCHEME_PREFIXES = ("http://", "https://")


def normalize_endpoint(endpoint: str) -> str:
    """
    Strip the scheme from an S3-compatible endpoint URL.

    The Minio SDK derives the scheme from the ``secure`` flag, so an endpoint
    must be host[:port] only. Accepting both ``minio:9000`` and
    ``http://minio:9000`` makes configuration more forgiving.
    """
    for prefix in _SCHEME_PREFIXES:
        if endpoint.startswith(prefix):
            return endpoint[len(prefix):]
    return endpoint

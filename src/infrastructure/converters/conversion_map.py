"""Conversion map: source format -> list of valid target formats.

Built directly from the converter registry so it always reflects what the
workers can actually perform.
"""

from functools import lru_cache

from src.infrastructure.converters.converter_registry import get_registry


@lru_cache(maxsize=1)
def _conversion_map_cached() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Build the immutable map once; the registry is static after import.

    Cached as nested tuples so a caller cannot mutate the cached value.
    """
    registry = get_registry()
    mapping: dict[str, set[str]] = {}
    for conversion in registry.list_conversions():
        mapping.setdefault(conversion.source_format, set()).add(conversion.target_format)

    return tuple(
        (source, tuple(sorted(targets))) for source, targets in mapping.items()
    )


def build_conversion_map() -> dict[str, list[str]]:
    """Return a mapping of ``source_format`` -> sorted ``target_format`` list.

    Example::

        {
            "pdf": ["docx", "epub", "md", ...],
            "xlsx": ["csv", "ods", "xls", ...],
        }
    """
    # Rebuild the (small) dict/list wrapper per call so callers get their own
    # mutable result, but never rescan the registry.
    return {source: list(targets) for source, targets in _conversion_map_cached()}

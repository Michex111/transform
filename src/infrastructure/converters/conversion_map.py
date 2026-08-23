"""Conversion map: source format -> list of valid target formats.

Built directly from the converter registry so it always reflects what the
workers can actually perform.
"""

from src.infrastructure.converters.converter_registry import get_registry


def build_conversion_map() -> dict[str, list[str]]:
    """Return a mapping of ``source_format`` -> sorted ``target_format`` list.

    Example::

        {
            "pdf": ["docx", "epub", "md", ...],
            "xlsx": ["csv", "ods", "xls", ...],
        }
    """
    registry = get_registry()
    mapping: dict[str, set[str]] = {}
    for conversion in registry.list_conversions():
        mapping.setdefault(conversion.source_format, set()).add(conversion.target_format)

    return {source: sorted(targets) for source, targets in mapping.items()}

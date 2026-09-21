from src.domain.conversions.value_object.conversion_type import ConversionType

from typing import Callable

type ConverterFunction = Callable[[str, str], None]

class ConverterRegistry:
    def __init__(self):
        self._registry: dict[ConversionType, ConverterFunction] = {}

    def register(self, *conversion_type: ConversionType) -> Callable[[ConverterFunction], ConverterFunction]:
        def decorator(func: ConverterFunction) -> ConverterFunction:
            for conversion in conversion_type:
                self._registry[conversion] = func
            return func
        return decorator

    def get_converter(self, conversion_type: ConversionType) -> ConverterFunction | None:
        return self._registry.get(conversion_type)
    
    def list_conversions(self) -> set[ConversionType]:
        return set(self._registry.keys())


def converter_output_extension(
    converter: ConverterFunction | None,
    default: str,
    input_path: str | None = None,
) -> str:
    """Output extension a converter declares for its result.

    By default a converted file is named ``<stem>.<target_format>``. A converter
    may override that by carrying an ``output_extension`` attribute, either

    * a plain extension string (e.g. ``"zip"``), or
    * a callable ``(input_path) -> str | None`` for outputs whose container
      depends on the input (e.g. ``pdf -> png`` zips the page images only when
      the PDF has more than one page).

    A callable that returns ``None``/``""`` (or that cannot be called because no
    ``input_path`` is available) keeps ``default``.
    """
    declared = getattr(converter, "output_extension", None)
    if declared is None:
        return default

    if callable(declared):
        if input_path is None:
            # The hook needs the downloaded input to decide, so a caller that
            # has not downloaded the object yet keeps the target extension.
            return default
        resolved = declared(input_path)
    else:
        resolved = declared

    if not resolved:
        return default
    return str(resolved).lstrip(".").lower()


# Create a global registry instance
converter_registry = ConverterRegistry()

def get_registry() -> ConverterRegistry:
    """
    Factory function that initializes and returns the converter registry.
    Imports converter functions to trigger their registration decorators.
    """
    # Import functions to register them with the global registry
    import src.infrastructure.converters.functions
    
    return converter_registry
from functools import wraps
from src.domain.value_object.conversion_type import ConversionType

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
from collections.abc import Callable

from src.domain.value_object.conversion_type import ConversionType

ConverterFunction = Callable[[str, str], None]


class FakeConverterRegistry:
    def __init__(self) -> None:
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
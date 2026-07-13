from pathlib import Path

import pytest

from src.domain.value_object.conversion_type import ConversionType
from src.infrastructure.converters.converter_registry import ConverterRegistry


@pytest.fixture
def converter_registry() -> ConverterRegistry:
    return ConverterRegistry()


@pytest.fixture
def registered_converter(converter_registry: ConverterRegistry, conversion_type: ConversionType) -> ConverterRegistry:
    @converter_registry.register(conversion_type)
    def uppercase_converter(input_path: str, output_path: str) -> None:
        source = Path(input_path).read_text(encoding="utf-8")
        Path(output_path).write_text(source.upper(), encoding="utf-8")

    return converter_registry
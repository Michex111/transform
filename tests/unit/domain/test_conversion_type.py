from dataclasses import FrozenInstanceError

import pytest

from src.domain.value_object.conversion_type import ConversionType


def test_conversion_type_equality_uses_value_semantics() -> None:
    left = ConversionType(source_format="pdf", target_format="docx")
    right = ConversionType(source_format="pdf", target_format="docx")
    different = ConversionType(source_format="pdf", target_format="txt")

    assert left == right
    assert left != different


def test_conversion_type_hashing_supports_dict_keys() -> None:
    conversion = ConversionType(source_format="txt", target_format="md")
    payload = {conversion: "registered"}

    assert payload[ConversionType(source_format="txt", target_format="md")] == "registered"


def test_conversion_type_is_immutable() -> None:
    conversion = ConversionType(source_format="txt", target_format="md")

    with pytest.raises(FrozenInstanceError):
        conversion.target_format = "pdf"  # type: ignore[misc]
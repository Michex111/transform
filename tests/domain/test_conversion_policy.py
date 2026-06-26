import pytest

from src.domain.exceptions import InvalidConversion
from src.domain.services.conversion_policy import is_supported
from src.domain.value_object.conversion_type import ConversionType


def test_conversion_policy_allows_supported_conversion() -> None:
    conversion = ConversionType(source_format="txt", target_format="md")
    supported = {conversion}

    assert is_supported(conversion, supported) is None


def test_conversion_policy_rejects_unsupported_conversion() -> None:
    requested = ConversionType(source_format="xlsx", target_format="pdf")
    supported = {ConversionType(source_format="txt", target_format="md")}

    with pytest.raises(InvalidConversion):
        is_supported(requested, supported)


def test_conversion_policy_rejects_when_supported_set_is_empty() -> None:
    requested = ConversionType(source_format="txt", target_format="pdf")

    with pytest.raises(InvalidConversion):
        is_supported(requested, set())
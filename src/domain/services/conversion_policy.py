from src.domain.value_object.conversion_type import ConversionType
from src.domain.exceptions import InvalidConversion

def is_supported(conversion_type: ConversionType, supported_conversions: set[ConversionType]):
    if conversion_type not in supported_conversions:
        raise InvalidConversion(f"Conversion type {conversion_type} is not supported")
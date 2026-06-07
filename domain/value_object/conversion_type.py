from dataclasses import dataclass

@dataclass(frozen=True)
class ConversionType:
    source_format: str
    target_format: str
    
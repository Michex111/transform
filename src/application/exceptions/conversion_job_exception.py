class ConversionJobException(Exception):
    """Custom exception for conversion job errors."""
    pass

class InvalidConversionJobError(ConversionJobException):
    """Raised when a conversion job is invalid."""
    pass

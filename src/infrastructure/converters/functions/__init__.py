# Import all converter modules to trigger their registration decorators
from src.infrastructure.converters.functions.document import pdf_docs_
from src.infrastructure.converters.functions import audio
from src.infrastructure.converters.functions import video
from src.infrastructure.converters.functions import image
from src.infrastructure.converters.functions import ebook
from src.infrastructure.converters.functions import archive

__all__ = ["pdf_docs_", "audio", "video", "image", "ebook", "archive"]

# Import all converter modules to trigger their registration decorators
from src.infrastructure.converters.functions import audio
from src.infrastructure.converters.functions import video
from src.infrastructure.converters.functions import image
from src.infrastructure.converters.functions import ebook
from src.infrastructure.converters.functions import archive
from src.infrastructure.converters.functions import office
from src.infrastructure.converters.functions import font
from src.infrastructure.converters.functions.document import pdf_docs_

__all__ = ["pdf_docs_", "audio", "video", "image", "ebook", "archive", "office", "font"]

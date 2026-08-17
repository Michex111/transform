"""Archive converters using Python standard library.

Supports: ZIP ↔ TAR.
"""

import os
import tarfile
import zipfile
import tempfile
import logging

from src.infrastructure.converters.converter_registry import converter_registry as registry
from src.domain.conversions.value_object.conversion_type import ConversionType

logger = logging.getLogger(__name__)

# Conversion type definitions
zip_to_tar = ConversionType("zip", "tar")
tar_to_zip = ConversionType("tar", "zip")


@registry.register(zip_to_tar)
def zip_to_tar_converter(input_file: str, output_file: str, logger_override=None) -> None:
    """Convert ZIP archive to TAR archive."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Extract ZIP
        with zipfile.ZipFile(input_file, "r") as zf:
            zf.extractall(tmpdir)
        # Create TAR
        with tarfile.open(output_file, "w") as tf:
            for root, _, files in os.walk(tmpdir):
                for f in files:
                    full_path = os.path.join(root, f)
                    arcname = os.path.relpath(full_path, tmpdir)
                    tf.add(full_path, arcname=arcname)


@registry.register(tar_to_zip)
def tar_to_zip_converter(input_file: str, output_file: str, logger_override=None) -> None:
    """Convert TAR archive to ZIP archive."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Extract TAR
        with tarfile.open(input_file, "r") as tf:
            tf.extractall(tmpdir)
        # Create ZIP
        with zipfile.ZipFile(output_file, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(tmpdir):
                for f in files:
                    full_path = os.path.join(root, f)
                    arcname = os.path.relpath(full_path, tmpdir)
                    zf.write(full_path, arcname=arcname)

"""
Audio converters using ffmpeg.

Supports: MP3 ↔ WAV, WAV ↔ FLAC, MP3 ↔ OGG, MP3 ↔ M4A.
"""

import subprocess
import logging

from src.infrastructure.converters.converter_registry import converter_registry as registry
from src.domain.conversions.value_object.conversion_type import ConversionType

logger = logging.getLogger(__name__)

# Conversion type definitions
mp3_to_wav = ConversionType("mp3", "wav")
wav_to_mp3 = ConversionType("wav", "mp3")
wav_to_flac = ConversionType("wav", "flac")
flac_to_wav = ConversionType("flac", "wav")
mp3_to_ogg = ConversionType("mp3", "ogg")
ogg_to_mp3 = ConversionType("ogg", "mp3")
mp3_to_m4a = ConversionType("mp3", "m4a")
m4a_to_mp3 = ConversionType("m4a", "mp3")


def _run_ffmpeg(input_file: str, output_file: str, codec_args: list[str] | None = None) -> None:
    """Run ffmpeg to convert an audio file."""
    cmd = ["ffmpeg", "-y", "-i", input_file]
    if codec_args:
        cmd.extend(codec_args)
    cmd.append(output_file)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")


@registry.register(mp3_to_wav)
def mp3_to_wav_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _run_ffmpeg(input_file, output_file, ["-acodec", "pcm_s16le"])


@registry.register(wav_to_mp3)
def wav_to_mp3_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _run_ffmpeg(input_file, output_file, ["-acodec", "libmp3lame", "-q:a", "2"])


@registry.register(wav_to_flac)
def wav_to_flac_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _run_ffmpeg(input_file, output_file, ["-acodec", "flac"])


@registry.register(flac_to_wav)
def flac_to_wav_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _run_ffmpeg(input_file, output_file, ["-acodec", "pcm_s16le"])


@registry.register(mp3_to_ogg)
def mp3_to_ogg_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _run_ffmpeg(input_file, output_file, ["-acodec", "libvorbis", "-q:a", "4"])


@registry.register(ogg_to_mp3)
def ogg_to_mp3_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _run_ffmpeg(input_file, output_file, ["-acodec", "libmp3lame", "-q:a", "2"])


@registry.register(mp3_to_m4a)
def mp3_to_m4a_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _run_ffmpeg(input_file, output_file, ["-acodec", "aac", "-b:a", "192k"])


@registry.register(m4a_to_mp3)
def m4a_to_mp3_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _run_ffmpeg(input_file, output_file, ["-acodec", "libmp3lame", "-q:a", "2"])

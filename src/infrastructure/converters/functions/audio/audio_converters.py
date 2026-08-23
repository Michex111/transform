"""Audio converters using ffmpeg.

Supports pairwise conversion between the audio formats in the UI catalog:
aac, aiff, alac, flac, m4a, m4b, mp3, ogg, opus, wav, wma.

``mid`` (MIDI) is a symbolic music format rather than sampled audio; ffmpeg
needs a MIDI renderer to convert it, which is not guaranteed to be installed,
so it is intentionally excluded from the pairwise matrix.
"""

import subprocess
import logging

from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.converters.converter_registry import converter_registry as registry

logger = logging.getLogger(__name__)

AUDIO_FORMATS = ["aac", "aiff", "alac", "flac", "m4a", "m4b", "mp3", "ogg", "opus", "wav", "wma"]

# ffmpeg infers the audio codec from the output container for most pairs.
# A small set of pairs needs an explicit codec to produce the right format.
CODEC_ARGS = {
    ("mp3", "wav"): ["-acodec", "pcm_s16le"],
    ("wav", "mp3"): ["-acodec", "libmp3lame", "-q:a", "2"],
    ("wav", "flac"): ["-acodec", "flac"],
    ("flac", "wav"): ["-acodec", "pcm_s16le"],
    ("mp3", "ogg"): ["-acodec", "libvorbis", "-q:a", "4"],
    ("ogg", "mp3"): ["-acodec", "libmp3lame", "-q:a", "2"],
    ("mp3", "m4a"): ["-acodec", "aac", "-b:a", "192k"],
    ("m4a", "mp3"): ["-acodec", "libmp3lame", "-q:a", "2"],
}


def _run_ffmpeg(input_file: str, output_file: str, codec_args: list[str] | None = None) -> None:
    """Run ffmpeg to convert an audio file."""
    cmd = ["ffmpeg", "-y", "-i", input_file]
    if codec_args:
        cmd.extend(codec_args)
    cmd.append(output_file)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")


def _make_converter(source: str, target: str):
    def converter(input_file: str, output_file: str, logger_override=None) -> None:
        del logger_override
        _run_ffmpeg(input_file, output_file, CODEC_ARGS.get((source, target)))

    converter.__name__ = f"audio_{source}_to_{target}"
    return converter


for _source in AUDIO_FORMATS:
    for _target in AUDIO_FORMATS:
        if _source == _target:
            continue
        registry.register(ConversionType(_source, _target))(
            _make_converter(_source, _target)
        )

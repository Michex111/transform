"""Video converters using ffmpeg.

Supports pairwise conversion between the video formats in the UI catalog:
mp4, mov, avi, mkv, webm, flv, wmv, mpg, m4v, 3gp, plus video -> gif.
"""

import subprocess
import logging

from src.domain.conversions.value_object.conversion_type import ConversionType
from src.infrastructure.converters.converter_registry import converter_registry as registry

logger = logging.getLogger(__name__)

VIDEO_FORMATS = ["mp4", "mov", "avi", "mkv", "webm", "flv", "wmv", "mpg", "m4v", "3gp"]

# Pairs that benefit from explicit encoder flags.
VIDEO_CODEC_ARGS = {
    ("mp4", "avi"): ["-c:v", "mpeg4", "-q:v", "5", "-c:a", "mp3"],
    ("avi", "mp4"): ["-c:v", "libx264", "-preset", "medium", "-c:a", "aac"],
    ("mp4", "mov"): ["-c:v", "libx264", "-c:a", "aac"],
    ("mov", "mp4"): ["-c:v", "libx264", "-preset", "medium", "-c:a", "aac"],
    ("avi", "mkv"): ["-c:v", "libx264", "-c:a", "aac"],
    ("mkv", "avi"): ["-c:v", "mpeg4", "-q:v", "5", "-c:a", "mp3"],
}


def _run_ffmpeg(input_file: str, output_file: str, extra_args: list[str] | None = None) -> None:
    """Run ffmpeg for video conversion."""
    cmd = ["ffmpeg", "-y", "-i", input_file]
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(output_file)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")


def _make_converter(source: str, target: str):
    def converter(input_file: str, output_file: str, logger_override=None) -> None:
        del logger_override
        _run_ffmpeg(input_file, output_file, VIDEO_CODEC_ARGS.get((source, target)))

    converter.__name__ = f"video_{source}_to_{target}"
    return converter


def video_to_gif_converter(input_file: str, output_file: str, logger_override=None) -> None:
    """Convert video to GIF (first 10 seconds, 10fps, 480p)."""
    del logger_override
    _run_ffmpeg(
        input_file,
        output_file,
        [
            "-t", "10",
            "-vf", "fps=10,scale=480:-1:flags=lanczos",
            "-c:v", "gif",
            "-f", "gif",
        ],
    )


for _source in VIDEO_FORMATS:
    for _target in VIDEO_FORMATS:
        if _source == _target:
            continue
        registry.register(ConversionType(_source, _target))(
            _make_converter(_source, _target)
        )

# video (mp4/mov/mkv/webm/avi) -> gif
for _source in ["mp4", "mov", "mkv", "webm", "avi"]:
    registry.register(ConversionType(_source, "gif"))(video_to_gif_converter)

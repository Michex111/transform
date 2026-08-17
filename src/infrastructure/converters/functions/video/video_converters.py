"""Video converters using ffmpeg.

Supports: MP4 ↔ AVI, MP4 ↔ MOV, AVI ↔ MKV, Video → GIF.
"""

import subprocess
import logging

from src.infrastructure.converters.converter_registry import converter_registry as registry
from src.domain.conversions.value_object.conversion_type import ConversionType

logger = logging.getLogger(__name__)

# Conversion type definitions
mp4_to_avi = ConversionType("mp4", "avi")
avi_to_mp4 = ConversionType("avi", "mp4")
mp4_to_mov = ConversionType("mp4", "mov")
mov_to_mp4 = ConversionType("mov", "mp4")
avi_to_mkv = ConversionType("avi", "mkv")
mkv_to_avi = ConversionType("mkv", "avi")
video_to_gif = ConversionType("mp4", "gif")


def _run_ffmpeg(input_file: str, output_file: str, extra_args: list[str] | None = None) -> None:
    """Run ffmpeg for video conversion."""
    cmd = ["ffmpeg", "-y", "-i", input_file]
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(output_file)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")


@registry.register(mp4_to_avi)
def mp4_to_avi_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _run_ffmpeg(input_file, output_file, ["-c:v", "mpeg4", "-q:v", "5", "-c:a", "mp3"])


@registry.register(avi_to_mp4)
def avi_to_mp4_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _run_ffmpeg(input_file, output_file, ["-c:v", "libx264", "-preset", "medium", "-c:a", "aac"])


@registry.register(mp4_to_mov)
def mp4_to_mov_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _run_ffmpeg(input_file, output_file, ["-c:v", "libx264", "-c:a", "aac"])


@registry.register(mov_to_mp4)
def mov_to_mp4_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _run_ffmpeg(input_file, output_file, ["-c:v", "libx264", "-preset", "medium", "-c:a", "aac"])


@registry.register(avi_to_mkv)
def avi_to_mkv_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _run_ffmpeg(input_file, output_file, ["-c:v", "libx264", "-c:a", "aac"])


@registry.register(mkv_to_avi)
def mkv_to_avi_converter(input_file: str, output_file: str, logger_override=None) -> None:
    _run_ffmpeg(input_file, output_file, ["-c:v", "mpeg4", "-q:v", "5", "-c:a", "mp3"])


@registry.register(video_to_gif)
def video_to_gif_converter(input_file: str, output_file: str, logger_override=None) -> None:
    """Convert video to GIF (first 10 seconds, 10fps, 480p)."""
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

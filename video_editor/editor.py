"""Video editor built on MoviePy 2.x.

Provides trim, concatenate, and text-overlay primitives that can be
composed into larger pipelines.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from moviepy import (
    CompositeVideoClip,
    TextClip,
    VideoFileClip,
    concatenate_videoclips,
)


DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


@dataclass
class TextOverlay:
    text: str
    start: float
    end: float
    fontsize: int = 48
    color: str = "white"
    stroke_color: str | None = "black"
    stroke_width: int = 2
    position: tuple | str = ("center", "bottom")
    font: str = DEFAULT_FONT


def trim(src: str | Path, start: float, end: float, dst: str | Path) -> Path:
    """Cut [start, end] seconds from `src` and write to `dst`."""
    src_path, dst_path = Path(src), Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with VideoFileClip(str(src_path)) as clip:
        if end <= start:
            raise ValueError(f"end ({end}) must be greater than start ({start})")
        if end > clip.duration:
            raise ValueError(
                f"end ({end}) exceeds clip duration ({clip.duration:.2f})"
            )
        sub = clip.subclipped(start, end)
        sub.write_videofile(str(dst_path), codec="libx264", audio_codec="aac")
    return dst_path


def concat(
    sources: Sequence[str | Path],
    dst: str | Path,
    method: str = "compose",
) -> Path:
    """Concatenate multiple clips into a single video.

    `method="compose"` resizes mismatched clips to a common frame.
    `method="chain"` is faster but requires identical resolution / fps.
    """
    if not sources:
        raise ValueError("sources must contain at least one path")
    dst_path = Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    clips = [VideoFileClip(str(p)) for p in sources]
    try:
        final = concatenate_videoclips(clips, method=method)
        final.write_videofile(str(dst_path), codec="libx264", audio_codec="aac")
    finally:
        for c in clips:
            c.close()
    return dst_path


def add_text(
    src: str | Path,
    overlays: Iterable[TextOverlay],
    dst: str | Path,
) -> Path:
    """Burn one or more text overlays into `src` and write to `dst`."""
    src_path, dst_path = Path(src), Path(dst)
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    with VideoFileClip(str(src_path)) as base:
        layers = [base]
        for o in overlays:
            duration = o.end - o.start
            if duration <= 0:
                raise ValueError(
                    f"overlay '{o.text!r}' has non-positive duration"
                )
            txt = (
                TextClip(
                    text=o.text,
                    font=o.font,
                    font_size=o.fontsize,
                    color=o.color,
                    stroke_color=o.stroke_color,
                    stroke_width=o.stroke_width,
                    method="caption",
                    size=(base.w - 80, None),
                )
                .with_start(o.start)
                .with_duration(duration)
                .with_position(o.position)
            )
            layers.append(txt)
        composed = CompositeVideoClip(layers)
        composed.write_videofile(
            str(dst_path), codec="libx264", audio_codec="aac"
        )
    return dst_path

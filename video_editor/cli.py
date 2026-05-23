"""Command-line interface for the MoviePy-based video editor."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from video_editor.editor import TextOverlay, add_text, concat, trim


def _parse_overlay(spec: str) -> TextOverlay:
    """Parse 'start:end:text' into a TextOverlay (text may contain ':')."""
    parts = spec.split(":", 2)
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            f"overlay must be 'start:end:text', got {spec!r}"
        )
    return TextOverlay(text=parts[2], start=float(parts[0]), end=float(parts[1]))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="video-editor")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_trim = sub.add_parser("trim", help="cut [start, end] from a clip")
    p_trim.add_argument("src", type=Path)
    p_trim.add_argument("dst", type=Path)
    p_trim.add_argument("--start", type=float, required=True)
    p_trim.add_argument("--end", type=float, required=True)

    p_concat = sub.add_parser("concat", help="concatenate multiple clips")
    p_concat.add_argument("sources", nargs="+", type=Path)
    p_concat.add_argument("--dst", type=Path, required=True)
    p_concat.add_argument("--method", choices=("compose", "chain"), default="compose")

    p_text = sub.add_parser("text", help="burn text overlays into a clip")
    p_text.add_argument("src", type=Path)
    p_text.add_argument("dst", type=Path)
    p_text.add_argument(
        "--overlay",
        action="append",
        required=True,
        type=_parse_overlay,
        metavar="START:END:TEXT",
        help="repeatable; e.g. --overlay 0:3:Hello",
    )

    args = p.parse_args(argv)

    if args.cmd == "trim":
        out = trim(args.src, args.start, args.end, args.dst)
    elif args.cmd == "concat":
        out = concat(args.sources, args.dst, method=args.method)
    elif args.cmd == "text":
        out = add_text(args.src, args.overlay, args.dst)
    else:
        p.error(f"unknown command {args.cmd!r}")
        return 2

    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

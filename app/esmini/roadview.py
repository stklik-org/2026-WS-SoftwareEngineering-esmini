"""Road previews in esmini's 3D viewer (FR-05, Tier 2): camera placement and the captured frame as PNG.

`odrviewer --custom_fixed_top_camera x,y,z,rot` looks straight down with a vertical field of view of 1 degree
(`ORTHO_FOV` in `ViewerBase/viewer.cpp`), so the camera height decides how much of the ground is visible. The
rotation pi/2 puts world x to the right and y up, as in the `plot` renderer (docs/spikes/02-rendering.md).
"""

from __future__ import annotations

import io
import math
from collections.abc import Sequence
from pathlib import Path

from app.esmini.roadplot import Border

FIELD_OF_VIEW_DEGREES = 1.0
MARGIN = 1.05  # visible extent / extent of the road network
NORTH_UP = math.pi / 2
TGA_HEADER_BYTES = 18


def fit_top_camera(borders: Sequence[Border], width: int, height: int) -> tuple[float, float, float, float]:
    """`x, y, z, rotation` of a top camera that shows every lane border in an image of `width` x `height`."""
    xs = [v for b in borders for v in b.x]
    ys = [v for b in borders for v in b.y]
    zs = [v for b in borders for v in b.z]
    visible_height = max(max(ys) - min(ys), (max(xs) - min(xs)) * height / width, 10.0) * MARGIN
    distance = visible_height / (2 * math.tan(math.radians(FIELD_OF_VIEW_DEGREES / 2)))
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, max(zs) + distance, NORTH_UP)


def last_frame(directory: Path) -> Path | None:
    frames = sorted(directory.glob("screen_shot_*.tga"))
    return frames[-1] if frames else None


def tga_to_png(data: bytes, width: int, height: int) -> bytes:
    """An uncompressed 24-bit TGA of `width` x `height` with bottom-left origin (esmini's capture format) as PNG.
    Raises ValueError for anything else, e.g. a frame truncated by a timeout."""
    from PIL import Image

    if len(data) < TGA_HEADER_BYTES:
        raise ValueError("truncated frame")
    id_length, image_type = data[0], data[2]
    w, h, bits, descriptor = (
        int.from_bytes(data[12:14], "little"),
        int.from_bytes(data[14:16], "little"),
        data[16],
        data[17],
    )
    pixels = data[TGA_HEADER_BYTES + id_length :]
    if (image_type, bits, w, h, descriptor & 0x20, len(pixels)) != (2, 24, width, height, 0, width * height * 3):
        raise ValueError(f"unexpected frame (type {image_type}, {bits} bit, {w}x{h}, {len(data)} bytes)")
    image = Image.frombytes("RGB", (width, height), pixels, "raw", "BGR", 0, -1)  # stride -1: bottom row first
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=False)
    return out.getvalue()

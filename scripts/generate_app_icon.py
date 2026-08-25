"""Generate the project-owned Conduit PNG and multi-resolution Windows icon."""

from __future__ import annotations

import argparse
import binascii
import math
from pathlib import Path
import struct
import zlib


ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _inside_rounded_square(x: float, y: float) -> bool:
    radius = 0.21
    half_inner = 0.5 - radius
    dx = max(abs(x - 0.5) - half_inner, 0.0)
    dy = max(abs(y - 0.5) - half_inner, 0.0)
    return dx * dx + dy * dy <= radius * radius


def _sample(x: float, y: float) -> tuple[int, int, int, int]:
    if not _inside_rounded_square(x, y):
        return 0, 0, 0, 0

    blend = max(0.0, min(1.0, (x + y) / 2.0))
    background = (
        round(12 * (1 - blend)),
        round(28 * (1 - blend) + 162 * blend),
        round(64 * (1 - blend) + 213 * blend),
        255,
    )

    dx = x - 0.5
    dy = y - 0.5
    distance = math.hypot(dx, dy)
    angle = math.degrees(math.atan2(dy, dx))
    ring = 0.205 <= distance <= 0.315 and abs(angle) >= 45
    endpoint_radius = 0.055
    endpoint_x = 0.5 + math.cos(math.radians(45)) * 0.26
    endpoint_offset = math.sin(math.radians(45)) * 0.26
    upper_endpoint = math.hypot(x - endpoint_x, y - (0.5 - endpoint_offset))
    lower_endpoint = math.hypot(x - endpoint_x, y - (0.5 + endpoint_offset))
    cap = min(upper_endpoint, lower_endpoint) <= endpoint_radius
    if ring or cap:
        node = min(upper_endpoint, lower_endpoint) <= 0.018
        return (66, 216, 255, 255) if node else (255, 255, 255, 255)
    return background


def _render_rgba(size: int, supersampling: int = 4) -> bytes:
    scale = size * supersampling
    pixels = bytearray()
    divisor = supersampling * supersampling
    for output_y in range(size):
        for output_x in range(size):
            totals = [0, 0, 0, 0]
            for sample_y in range(supersampling):
                y = (output_y * supersampling + sample_y + 0.5) / scale
                for sample_x in range(supersampling):
                    x = (output_x * supersampling + sample_x + 0.5) / scale
                    color = _sample(x, y)
                    for channel, value in enumerate(color):
                        totals[channel] += value
            pixels.extend(round(total / divisor) for total in totals)
    return bytes(pixels)


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    checksum = binascii.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)


def _encode_png(size: int) -> bytes:
    rgba = _render_rgba(size)
    stride = size * 4
    scanlines = b"".join(
        b"\x00" + rgba[offset : offset + stride]
        for offset in range(0, len(rgba), stride)
    )
    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(scanlines, level=9))
        + _png_chunk(b"IEND", b"")
    )


def _encode_ico(images: list[tuple[int, bytes]]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(images))
    directory = bytearray()
    payload = bytearray()
    offset = len(header) + 16 * len(images)
    for size, image in images:
        dimension = 0 if size == 256 else size
        directory.extend(
            struct.pack(
                "<BBBBHHII",
                dimension,
                dimension,
                0,
                0,
                1,
                32,
                len(image),
                offset,
            )
        )
        payload.extend(image)
        offset += len(image)
    return header + bytes(directory) + bytes(payload)


def generate(output_directory: Path) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    images = [(size, _encode_png(size)) for size in ICON_SIZES]
    (output_directory / "app_icon.png").write_bytes(images[-1][1])
    (output_directory / "app_icon.ico").write_bytes(_encode_ico(images))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "app" / "assets",
    )
    arguments = parser.parse_args(argv)
    generate(arguments.output_directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

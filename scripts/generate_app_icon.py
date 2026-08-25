"""Generate tightly cropped, centered Conduit PNG and Windows icon assets."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "app" / "assets" / "app_icon_source.png"
DEFAULT_OUTPUT_DIRECTORY = PROJECT_ROOT / "app" / "assets"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
ALPHA_THRESHOLD = 8


def visible_crop(image: Image.Image) -> Image.Image:
    """Return the smallest rectangle containing pixels above the alpha threshold."""
    rgba = image.convert("RGBA")
    visible_alpha = rgba.getchannel("A").point(
        lambda value: 255 if value > ALPHA_THRESHOLD else 0
    )
    bounds = visible_alpha.getbbox()
    if bounds is None:
        raise ValueError("The icon source PNG contains no visible pixels.")
    return rgba.crop(bounds)


def centered_icon(source: Image.Image, size: int) -> Image.Image:
    """Fit visible artwork nearly edge-to-edge and center it on a square canvas."""
    content = visible_crop(source)
    usable_size = max(1, size - 2)
    scale = min(usable_size / content.width, usable_size / content.height)
    dimensions = (
        max(1, round(content.width * scale)),
        max(1, round(content.height * scale)),
    )
    resized = content.resize(dimensions, Image.Resampling.LANCZOS)
    resized = visible_crop(resized)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    position = (
        (size - resized.width) // 2,
        (size - resized.height) // 2,
    )
    canvas.alpha_composite(resized, position)
    return canvas


def generate_icon_assets(source_path: Path, output_directory: Path) -> None:
    if not source_path.is_file():
        raise FileNotFoundError("The project-owned icon source PNG was not found.")
    output_directory.mkdir(parents=True, exist_ok=True)

    with Image.open(source_path) as source:
        images = {size: centered_icon(source, size) for size in ICON_SIZES}

    preview = images[256]
    preview.save(output_directory / "app_icon.png", format="PNG", optimize=False)
    preview.save(
        output_directory / "app_icon.ico",
        format="ICO",
        sizes=[(size, size) for size in ICON_SIZES],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    generate_icon_assets(args.source.resolve(), args.output_dir.resolve())


if __name__ == "__main__":
    main()
